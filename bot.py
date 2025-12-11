#!/usr/bin/env python3
# bot.py - Polém Store (versão final)
# Telebot + Flask webhook Mercado Pago + SQLite (db.py)
# Organização: seções claras, compatível com schema fornecido.

import os
import time
import json
import uuid
import base64
import threading
import sqlite3
from io import BytesIO

import requests
from flask import Flask, request, jsonify

import telebot
from telebot.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)

# Import da camada de dados (db.py)
from db import (
    ensure_user, get_user_by_telegram, get_user_by_id,
    get_balance, debit_balance, credit_balance,
    add_transaction, approve_transaction_by_mp_id, get_transaction_by_mp_id,
    get_approved_history, list_products, get_product, get_available_access, mark_access_sold,
    add_product, add_product_access, is_admin_level, add_admin_db, remove_admin_db, list_admins_db,
    ban_user_db, unban_user_db, is_banned_db,
    get_sales_report, register_sale
)

# -------------------------
# CONFIGURAÇÕES / TOKENS
# -------------------------
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN") or "Insira Aqui O Token De Seu Bot Telegram"
MP_ACCESS_TOKEN = os.environ.get("MP_ACCESS_TOKEN") or "insira aqui seu Token API, da gateway selecionada, projeto adaptado para Mercado Pago"
CLOUDFLARE_SUBDOMAIN = os.environ.get("CLOUDFLARE_SUBDOMAIN") or None

if CLOUDFLARE_SUBDOMAIN:
    WEBHOOK_BASE_URL = f"utilize seu dominio"
else:
    WEBHOOK_BASE_URL = None

STORE_NAME = "Polém Store🐝"
DB_PATH = "store.db"

# Inicializa Telebot (pyTelegramBotAPI)
bot = telebot.TeleBot(TELEGRAM_TOKEN, parse_mode="HTML")

# Flask (webhook)
app = Flask(__name__)

# -------------------------
# UTIL: keyboard principal
# -------------------------
def main_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("💰 Gerar PIX"), KeyboardButton("📊 Saldo"))
    kb.add(KeyboardButton("📄 Histórico"), KeyboardButton("👤 Perfil"))
    kb.add(KeyboardButton("🛒 Comprar"), KeyboardButton("✍️ Sugestão"))
    return kb

# -------------------------
# Helper: Mercado Pago - criar PIX
# -------------------------
def mp_create_pix(amount: float, description: str, external_reference: str):
    """
    Cria pagamento PIX no Mercado Pago v1 Payments.
    - Adiciona header X-Idempotency-Key
    - Usa payer.email com plus-addressing para evitar validações
    - Se CLOUDFLARE_SUBDOMAIN definido, inclui notification_url
    """
    url = "https://api.mercadopago.com/v1/payments"
    idempotency_key = str(uuid.uuid4())
    headers = {
        "Authorization": f"Bearer {MP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
        "X-Idempotency-Key": idempotency_key
    }

    payer_email = f"pagador+{external_reference}@gmail.com"

    payload = {
        "transaction_amount": float(amount),
        "description": description,
        "payment_method_id": "pix",
        "external_reference": external_reference,
        "payer": {
            "email": payer_email,
            "first_name": "Cliente",
            "last_name": "Polém",
            "identification": {"type": "CPF", "number": "00000000000"}
        }
    }

    if WEBHOOK_BASE_URL:
        payload["notification_url"] = f"{WEBHOOK_BASE_URL}/mp/webhook"

    resp = requests.post(url, json=payload, headers=headers, timeout=25)
    resp.raise_for_status()
    data = resp.json()

    # Extrair QR / QR base64 em diferentes formatos
    poi = data.get("point_of_interaction") or data.get("pointofinteraction") or {}
    txdata = poi.get("transaction_data") or poi.get("transactiondata") or {}
    qr_code = txdata.get("qr_code") or txdata.get("qrCode") or data.get("qr_code")
    qr_b64 = txdata.get("qr_code_base64") or txdata.get("qrCodeBase64") or data.get("qr_code_base64")

    return {
        "payment_id": str(data.get("id")) if data.get("id") is not None else None,
        "status": data.get("status"),
        "qr_code": qr_code,
        "qr_code_base64": qr_b64,
        "raw": data,
        "idempotency_key": idempotency_key
    }

def mp_get_payment(payment_id: str):
    url = f"https://api.mercadopago.com/v1/payments/{payment_id}"
    headers = {"Authorization": f"Bearer {MP_ACCESS_TOKEN}"}
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.json()

# -------------------------
# Comandos básicos /start e botões
# -------------------------
@bot.message_handler(commands=["start"])
def cmd_start(message):
    tg = message.from_user
    ensure_user(tg.id, tg.username, tg.first_name, tg.last_name)
    # se banido, bloqueia
    if is_banned_db(tg.id):
        bot.send_message(message.chat.id, "🚫 Você está banido da Polém Store.")
        return

    texto = (
        f"🐝 <b>{STORE_NAME}</b>\n\n"
        "⚠️ Regras:\n"
        "- PIX valor mínimo: R$10\n"
        "- Tempo limite de pagamento: 10 minutos\n"
        "- Aguarde 5–10 minutos após o pagamento para compensação\n\n"
        "Comandos:\n"
        "💰 /pix VALOR – gerar pagamento\n"
        "📊 /saldo – ver saldo\n"
        "📄 /historico – histórico\n"
        "👤 /perfil – perfil\n"
        "🛒 /comprar – comprar produtos\n"
        "✍️ /sugestao TEXTO – enviar sugestão\n"
        "🔑 /admin SENHA – painel admin"
    )
    bot.send_message(message.chat.id, texto, reply_markup=main_keyboard())

# Mapear teclado para comandos
@bot.message_handler(func=lambda m: m.text == "📊 Saldo")
def saldo_btn(m): cmd_saldo(m)

@bot.message_handler(func=lambda m: m.text == "📄 Histórico")
def historico_btn(m): cmd_historico(m)

@bot.message_handler(func=lambda m: m.text == "👤 Perfil")
def perfil_btn(m): cmd_perfil(m)

@bot.message_handler(func=lambda m: m.text == "🛒 Comprar")
def comprar_btn(m): cmd_comprar(m)

@bot.message_handler(func=lambda m: m.text == "✍️ Sugestão")
def sugestao_btn(m):
    bot.reply_to(m, "✉️ Envie sua sugestão assim: /sugestao texto da sugestão")

@bot.message_handler(func=lambda m: m.text == "💰 Gerar PIX")
def gerar_pix_btn(m):
    bot.reply_to(m, "Use o comando: /pix VALOR (ex: /pix 20.00) — valor mínimo R$10.00")

# -------------------------
# Saldo / Perfil / Histórico
# -------------------------
@bot.message_handler(commands=["saldo"])
def cmd_saldo(message):
    tg = message.from_user
    ensure_user(tg.id, tg.username, tg.first_name, tg.last_name)
    bal = get_balance(tg.id)
    bot.reply_to(message, f"💰 Seu saldo atual é: R$ {bal:.2f}")

@bot.message_handler(commands=["perfil"])
def cmd_perfil(message):
    tg = message.from_user
    ensure_user(tg.id, tg.username, tg.first_name, tg.last_name)
    bal = get_balance(tg.id)
    bot.reply_to(message,
        f"👤 Perfil\n"
        f"Usuário: @{tg.username or '—'}\n"
        f"Nome: {tg.first_name or ''} {tg.last_name or ''}\n"
        f"💰 Saldo: R${bal:.2f}"
    )

@bot.message_handler(commands=["historico"])
def cmd_historico(message):
    tg = message.from_user
    rows = get_approved_history(tg.id, limit=20)
    if not rows:
        bot.reply_to(message, "🔍 Nenhuma transação aprovada encontrada.")
        return
    texto = "📜 Histórico de recargas aprovadas:\n\n"
    for r in rows:
        dt = (r.get("approved_at") or r.get("created_at") or "")[:19]
        texto += f"• R${float(r['amount']):.2f} — {dt} — ID {r.get('mp_id')}\n"
    bot.reply_to(message, texto)

# -------------------------
# /pix - gerar cobrança PIX
# -------------------------
@bot.message_handler(commands=["pix"])
def cmd_pix(message):
    try:
        MIN_VALUE = 10.0
        parts = message.text.split()
        if len(parts) < 2:
            bot.reply_to(message, f"⚠️ Use: /pix VALOR (mínimo R$ {MIN_VALUE:.2f})")
            return

        try:
            value = float(parts[1].replace(",", "."))
        except Exception:
            bot.reply_to(message, "⚠️ Valor inválido. Use números como 10 ou 25.50")
            return

        if value < MIN_VALUE:
            bot.reply_to(message, f"⚠️ Valor mínimo: R$ {MIN_VALUE:.2f}")
            return

        tg = message.from_user
        user_id = ensure_user(tg.id, tg.username, tg.first_name, tg.last_name)

        desc = f"Recarga {STORE_NAME} - {tg.id}"
        ext_ref = f"{tg.id}_{int(time.time())}"

        try:
            mp_resp = mp_create_pix(value, desc, ext_ref)
        except requests.HTTPError as e:
            try:
                status_code = e.response.status_code
                text = e.response.text
                bot.reply_to(message, f"❌ Erro HTTP ao gerar PIX: {status_code} - {text}")
            except Exception:
                bot.reply_to(message, f"❌ Erro HTTP ao gerar PIX: {e}")
            return
        except Exception as e:
            bot.reply_to(message, f"❌ Erro ao gerar PIX: {e}")
            return

        payment_id = mp_resp.get("payment_id")
        qr_code_str = mp_resp.get("qr_code")
        qr_code_b64 = mp_resp.get("qr_code_base64")

        if not payment_id:
            bot.reply_to(message, "❌ Não foi possível gerar o PIX no momento. Tente novamente mais tarde.")
            return

        # registrar transação pendente
        add_transaction(user_id, payment_id, value, status="pending", description=desc, raw_json=mp_resp.get("raw"))

        # envia instruções + copia e cola + qr
        text = (
            f"💰 PIX gerado!\n\n"
            f"🧾 <b>ID pagamento:</b> {payment_id}\n"
            f"💸 <b>Valor:</b> R$ {value:.2f}\n\n"
            f"⏱️ Pague em até 10 minutos.\n"
            f"🔄 Após pagar, a compensação automática pode levar alguns minutos.\n\n"
        )
        if qr_code_str:
            text += f"📋 <b>Copia-e-cola PIX:</b>\n<code>{qr_code_str}</code>\n\n"

        bot.send_message(message.chat.id, text, parse_mode="HTML")

        if qr_code_b64:
            try:
                img_bytes = base64.b64decode(qr_code_b64)
                bio = BytesIO(img_bytes)
                bio.name = "qrcode.png"
                bot.send_photo(message.chat.id, photo=bio, caption="📷 QR Code PIX")
            except Exception as e:
                print("Erro ao enviar QR image:", e)

    except requests.HTTPError as e:
        bot.reply_to(message, f"❌ Erro HTTP ao gerar PIX: {e}")
    except Exception as e:
        bot.reply_to(message, f"❌ Erro ao gerar PIX: {e}")

# -------------------------
# /comprar - lista produtos (inline buttons) e callback
# -------------------------
@bot.message_handler(commands=["comprar"])
def cmd_comprar(message):
    produtos = list_products()
    if not produtos:
        bot.reply_to(message, "📦 Nenhum produto disponível no momento.")
        return

    markup = InlineKeyboardMarkup()
    for p in produtos:
        label = f"{p['name']} - R${float(p['price']):.2f}"
        markup.add(InlineKeyboardButton(label, callback_data=f"buy_{p['id']}"))

    bot.send_message(message.chat.id, "🛒 Escolha um produto:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data and call.data.startswith("buy_"))
def callback_buy(call):
    try:
        tg = call.from_user
        product_id = int(call.data.split("_", 1)[1])

        product = get_product(product_id)
        if not product:
            bot.answer_callback_query(call.id, "❌ Produto não encontrado.", show_alert=True)
            return

        balance = get_balance(tg.id)
        price = float(product["price"])

        if balance < price:
            bot.answer_callback_query(call.id, f"⚠️ Saldo insuficiente. Gere PIX com /pix {price:.2f}", show_alert=True)
            return

        # pega acesso disponível (tratamos colunas 'password' ou 'senha' no db)
        access = get_available_access(product_id)
        if not access:
            bot.answer_callback_query(call.id, "📦 Produto esgotado. Nenhum acesso disponível.", show_alert=True)
            return

        # marca vendido e debita
        mark_access_sold(access["id"])
        debit_balance(tg.id, price)

        # registra venda (opcional): usa register_sale se presente
        try:
            user_row = get_user_by_telegram(tg.id)
            if user_row:
                register_sale(user_row["id"], product_id, price, quantity=1)
        except Exception:
            pass

        # mensagem com credenciais
        text = (
            f"✅ Compra realizada!\n\n"
            f"📦 Produto: {product['name']}\n"
            f"💸 Valor: R${price:.2f}\n\n"
            f"🔑 Acesso: {access['login']}\n"
            f"🔐 Senha: {access.get('password') or access.get('senha')}\n\n"
            "Obrigado por comprar na Polém Store 🐝"
        )
        bot.send_message(call.message.chat.id, text)
        bot.answer_callback_query(call.id)
    except Exception as e:
        bot.answer_callback_query(call.id, f"❌ Erro na compra: {e}", show_alert=True)

# -------------------------
# Sugestão
# -------------------------
@bot.message_handler(commands=["sugestao"])
def cmd_sugestao(message):
    texto = message.text.replace("/sugestao", "").strip()
    if not texto:
        bot.reply_to(message, "✉️ Envie sua sugestão assim: /sugestao texto da sugestão")
        return
    # Poderíamos salvar em uma tabela 'suggestions' — hoje apenas confirma
    bot.reply_to(message, "✅ Obrigado pela sugestão! Ela foi registrada.")

# -------------------------
# ADMIN: painel e comandos
# - Nível 1: suporte/monitoramento (ban/unban, responder sugestões) - nivel >=1
# - Nível 2: super-admin (produtos, acessos, saldo, aprovar pix, add/remove admins) - nivel >=2
# -------------------------
def _is_admin_level(user_telegram_id:int, senha: str = None, min_level: int = 1) -> bool:
    return is_admin_level(user_telegram_id, senha, min_level=min_level)

@bot.message_handler(commands=["admin"])
def cmd_admin(message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "❌ Use: /admin SENHA")
        return
    senha = parts[1].strip()
    tg = message.from_user
    if not _is_admin_level(tg.id, senha, min_level=1):
        bot.reply_to(message, "🚫 Acesso negado. Senha incorreta ou você não é admin.")
        return

    # Identificar nível real (sem expor senha)
    # buscamos na tabela admins pelo telegram_id (senha ok garantiu min_level)
    # Mostrar menu simples via texto (para comandos explícitos)
    bot.reply_to(message,
        "🔐 Painel Admin — comandos:\n"
        "Nível 1 (suporte): /ban TELEGRAMID | SENHA_ADMIN, /unban TELEGRAMID | SENHA_ADMIN, /admins LISTA\n"
        "Nível 2 (super): /addproduto NOME | PRECO | SENHA_ADMIN, /editproduto ID | NOME | PRECO | SENHA_ADMIN, /delproduto ID | SENHA_ADMIN\n"
        "/addacesso PRODUTOID | LOGIN | PASSWORD | SENHA_ADMIN\n"
        "/addsaldo TELEGRAMID | VALOR | SENHA_ADMIN\n"
        "/aprovarpix PAYMENTID | SENHA_ADMIN\n"
        "/addadmin TELEGRAMID | NOME | SENHA_ADMIN | NIVEL\n"
        "/rmadmin TELEGRAMID | SENHA_ADMIN\n"
        "/report PERIOD (total/daily/weekly/monthly) | SENHA_ADMIN"
    )

# /addadmin TELEGRAMID | NOME | SENHA_ADMIN | NIVEL  (somente nível 2)
@bot.message_handler(commands=["addadmin"])
def cmd_addadmin(message):
    try:
        payload = message.text.replace("/addadmin", "").strip()
        if not payload or "|" not in payload:
            bot.reply_to(message, "❌ Use: /addadmin TELEGRAMID | NOME | SENHA_ADMIN | NIVEL")
            return
        target_tg_s, nome, senha_admin, nivel_s = [p.strip() for p in payload.split("|", 3)]
        target_tg = int(target_tg_s)
        nivel = int(nivel_s)
        # checar quem executa
        if not _is_admin_level(message.from_user.id, senha_admin, min_level=2):
            bot.reply_to(message, "🚫 Apenas admins nível 2 podem adicionar admins.")
            return
        # adicionar
        add_admin_db(target_tg, nome, senha_admin, nivel)
        bot.reply_to(message, f"✅ Admin adicionado: {target_tg} (nível {nivel}).")
    except Exception as e:
        bot.reply_to(message, f"❌ Erro ao adicionar admin: {e}")

# /rmadmin TELEGRAMID | SENHA_ADMIN (nível 2)
@bot.message_handler(commands=["rmadmin"])
def cmd_rmadmin(message):
    try:
        payload = message.text.replace("/rmadmin", "").strip()
        if not payload or "|" not in payload:
            bot.reply_to(message, "❌ Use: /rmadmin TELEGRAMID | SENHA_ADMIN")
            return
        target_tg_s, senha_admin = [p.strip() for p in payload.split("|", 1)]
        target_tg = int(target_tg_s)
        if not _is_admin_level(message.from_user.id, senha_admin, min_level=2):
            bot.reply_to(message, "🚫 Apenas admins nível 2 podem remover admins.")
            return
        remove_admin_db(target_tg)
        bot.reply_to(message, f"✅ Admin {target_tg} removido.")
    except Exception as e:
        bot.reply_to(message, f"❌ Erro ao remover admin: {e}")

# /admins - listar admins (nivel 1+)
@bot.message_handler(commands=["admins"])
def cmd_list_admins(message):
    try:
        parts = message.text.split(maxsplit=1)
        senha = parts[1].strip() if len(parts) > 1 else None
        if not _is_admin_level(message.from_user.id, senha, min_level=1):
            bot.reply_to(message, "🚫 Apenas admins podem ver a lista de admins.")
            return
        rows = list_admins_db()
        if not rows:
            bot.reply_to(message, "Nenhum admin cadastrado.")
            return
        texto = "🔐 Admins cadastrados:\n"
        for r in rows:
            texto += f"• {r.get('telegram_id') or r.get('telegram_id')}: {r.get('name') or r.get('nome','-')} (nível {r.get('level') or r.get('nivel')})\n"
        bot.reply_to(message, texto)
    except Exception as e:
        bot.reply_to(message, f"❌ Erro ao listar admins: {e}")

# /ban TELEGRAMID | SENHA_ADMIN  (nivel 1+ can ban non-admins)
@bot.message_handler(commands=["ban"])
def cmd_ban(message):
    try:
        payload = message.text.replace("/ban", "").strip()
        if not payload or "|" not in payload:
            bot.reply_to(message, "❌ Use: /ban TELEGRAMID | SENHA_ADMIN")
            return
        target_s, senha = [p.strip() for p in payload.split("|", 1)]
        target_tg = int(target_s)
        # quem executa must be admin level >=1
        if not _is_admin_level(message.from_user.id, senha, min_level=1):
            bot.reply_to(message, "🚫 Apenas admins podem banir.")
            return
        # cannot ban other admins
        try:
            # check if target is admin (any level)
            if is_admin_level(target_tg, None, min_level=1):
                bot.reply_to(message, "🚫 Não é permitido banir outro admin.")
                return
        except Exception:
            pass
        ban_user_db(target_tg)
        bot.reply_to(message, f"✅ Usuário {target_tg} banido.")
    except Exception as e:
        bot.reply_to(message, f"❌ Erro ao banir usuário: {e}")

# /unban TELEGRAMID | SENHA_ADMIN
@bot.message_handler(commands=["unban"])
def cmd_unban(message):
    try:
        payload = message.text.replace("/unban", "").strip()
        if not payload or "|" not in payload:
            bot.reply_to(message, "❌ Use: /unban TELEGRAMID | SENHA_ADMIN")
            return
        target_s, senha = [p.strip() for p in payload.split("|", 1)]
        target_tg = int(target_s)
        if not _is_admin_level(message.from_user.id, senha, min_level=1):
            bot.reply_to(message, "🚫 Apenas admins podem desbanir.")
            return
        unban_user_db(target_tg)
        bot.reply_to(message, f"✅ Usuário {target_tg} desbanido.")
    except Exception as e:
        bot.reply_to(message, f"❌ Erro ao desbanir usuário: {e}")

# /addproduto NOME | PRECO | SENHA_ADMIN  (nível 2)
@bot.message_handler(commands=["addproduto"])
def cmd_addproduto(message):
    try:
        payload = message.text.replace("/addproduto", "").strip()
        if not payload or "|" not in payload:
            bot.reply_to(message, "❌ Use: /addproduto NOME | PRECO | SENHA_ADMIN")
            return
        name, price_str, senha = [p.strip() for p in payload.split("|", 2)]
        if not _is_admin_level(message.from_user.id, senha, min_level=2):
            bot.reply_to(message, "🚫 Apenas admins nível 2 podem adicionar produtos.")
            return
        price = float(price_str.replace(",", "."))
        pid = add_product(name, price)
        bot.reply_to(message, f"✅ Produto '{name}' adicionado (id: {pid}) por R$ {price:.2f}")
    except Exception as e:
        bot.reply_to(message, f"❌ Erro ao adicionar produto: {e}")

# /editproduto ID | NOME | PRECO | SENHA_ADMIN  (nível 2)
@bot.message_handler(commands=["editproduto"])
def cmd_editproduto(message):
    try:
        payload = message.text.replace("/editproduto", "").strip()
        if not payload or "|" not in payload:
            bot.reply_to(message, "❌ Use: /editproduto ID | NOME | PRECO | SENHA_ADMIN")
            return
        pid_s, name, price_str, senha = [p.strip() for p in payload.split("|", 3)]
        pid = int(pid_s)
        if not _is_admin_level(message.from_user.id, senha, min_level=2):
            bot.reply_to(message, "🚫 Apenas admins nível 2 podem editar produtos.")
            return
        price = float(price_str.replace(",", "."))
        # update_product isn't in your db.py earlier; we'll do direct SQL here for safety
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("UPDATE products SET name = ?, price = ? WHERE id = ?", (name, price, pid))
        conn.commit()
        conn.close()
        bot.reply_to(message, f"✅ Produto {pid} atualizado: {name} — R$ {price:.2f}")
    except Exception as e:
        bot.reply_to(message, f"❌ Erro ao editar produto: {e}")

# /delproduto ID | SENHA_ADMIN (nível 2)
@bot.message_handler(commands=["delproduto"])
def cmd_delproduto(message):
    try:
        payload = message.text.replace("/delproduto", "").strip()
        if not payload or "|" not in payload:
            bot.reply_to(message, "❌ Use: /delproduto ID | SENHA_ADMIN")
            return
        pid_s, senha = [p.strip() for p in payload.split("|", 1)]
        pid = int(pid_s)
        if not _is_admin_level(message.from_user.id, senha, min_level=2):
            bot.reply_to(message, "🚫 Apenas admins nível 2 podem remover produtos.")
            return
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("DELETE FROM products WHERE id = ?", (pid,))
        conn.commit()
        conn.close()
        bot.reply_to(message, f"✅ Produto {pid} removido.")
    except Exception as e:
        bot.reply_to(message, f"❌ Erro ao remover produto: {e}")

# /addacesso PRODUTOID | LOGIN | PASSWORD | SENHA_ADMIN (nível 2)
@bot.message_handler(commands=["addacesso"])
def cmd_addacesso(message):
    try:
        payload = message.text.replace("/addacesso", "").strip()
        if not payload or "|" not in payload:
            bot.reply_to(message, "❌ Use: /addacesso PRODUTOID | LOGIN | PASSWORD | SENHA_ADMIN")
            return
        prodid_s, login, password, senha = [p.strip() for p in payload.split("|", 3)]
        product_id = int(prodid_s)
        if not _is_admin_level(message.from_user.id, senha, min_level=2):
            bot.reply_to(message, "🚫 Apenas admins nível 2 podem adicionar acessos.")
            return
        aid = add_product_access(product_id, login, password)
        bot.reply_to(message, f"✅ Acesso adicionado (id: {aid}) ao produto {product_id}: {login}/{password}")
    except Exception as e:
        bot.reply_to(message, f"❌ Erro ao adicionar acesso: {e}")

# /addsaldo TELEGRAMID | VALOR | SENHA_ADMIN (nível 2)
@bot.message_handler(commands=["addsaldo"])
def cmd_addsaldo(message):
    try:
        payload = message.text.replace("/addsaldo", "").strip()
        if not payload or "|" not in payload:
            bot.reply_to(message, "❌ Use: /addsaldo TELEGRAMID | VALOR | SENHA_ADMIN")
            return
        tg_target_s, value_s, senha = [p.strip() for p in payload.split("|", 2)]
        target_tg = int(tg_target_s)
        amount = float(value_s.replace(",", "."))
        if not _is_admin_level(message.from_user.id, senha, min_level=2):
            bot.reply_to(message, "🚫 Apenas admins nível 2 podem adicionar saldo.")
            return
        # garantir que o usuário exista
        ensure_user(target_tg, None, None, None)
        credit_balance(target_tg, amount)
        bot.reply_to(message, f"✅ Saldo R$ {amount:.2f} creditado ao usuário {target_tg}.")
    except Exception as e:
        bot.reply_to(message, f"❌ Erro ao creditar saldo: {e}")

# /aprovarpix PAYMENTID | SENHA_ADMIN (nível 2) - manual fallback
@bot.message_handler(commands=["aprovarpix"])
def cmd_aprovarpix(message):
    try:
        payload = message.text.replace("/aprovarpix", "").strip()
        if not payload or "|" not in payload:
            bot.reply_to(message, "❌ Use: /aprovarpix PAYMENTID | SENHA_ADMIN")
            return
        payment_id, senha = [p.strip() for p in payload.split("|", 1)]
        if not _is_admin_level(message.from_user.id, senha, min_level=2):
            bot.reply_to(message, "🚫 Apenas admins nível 2 podem aprovar pagamentos manualmente.")
            return

        info = mp_get_payment(payment_id)
        status = info.get("status")
        if status not in ("approved", "accredited", "paid"):
            bot.reply_to(message, f"⚠️ Pagamento {payment_id} ainda não aprovado (status: {status}).")
            return

        changed = approve_transaction_by_mp_id(payment_id)
        if changed:
            tx = get_transaction_by_mp_id(payment_id)
            if tx:
                amount = float(tx["amount"])
                user_db_id = tx["user_id"]
                # buscar telegram_id real
                conn = sqlite3.connect(DB_PATH)
                cur = conn.cursor()
                cur.execute("SELECT telegram_id FROM users WHERE id = ?", (user_db_id,))
                row = cur.fetchone()
                conn.close()
                if row:
                    telegram_id = int(row[0])
                    credit_balance(telegram_id, amount)
                    bot.reply_to(message, f"✅ Pagamento {payment_id} aprovado manualmente. R$ {amount:.2f} creditado ao {telegram_id}.")
                    return
        bot.reply_to(message, f"⚠️ Transação {payment_id} não encontrada ou já aprovada.")
    except Exception as e:
        bot.reply_to(message, f"❌ Erro em aprovarpix: {e}")

# /report PERIOD | SENHA_ADMIN (period: total/daily/weekly/monthly) - nível 2
@bot.message_handler(commands=["report"])
def cmd_report(message):
    try:
        payload = message.text.replace("/report", "").strip()
        if not payload or "|" not in payload:
            bot.reply_to(message, "❌ Use: /report PERIOD | SENHA_ADMIN (period: total/daily/weekly/monthly)")
            return
        period, senha = [p.strip() for p in payload.split("|", 1)]
        if not _is_admin_level(message.from_user.id, senha, min_level=2):
            bot.reply_to(message, "🚫 Apenas admins nível 2 podem acessar relatórios.")
            return
        period = period.lower()
        if period not in ("total", "daily", "weekly", "monthly"):
            bot.reply_to(message, "❌ Período inválido. Use total/daily/weekly/monthly.")
            return
        report = get_sales_report(period)
        bot.reply_to(message, f"📊 Relatório ({period}):\nVendas: {report['count']}\nTotal: R$ {float(report['total']):.2f}")
    except Exception as e:
        bot.reply_to(message, f"❌ Erro ao gerar relatório: {e}")

# -------------------------
# Webhook Mercado Pago - /mp/webhook
# -------------------------
@app.route("/mp/webhook", methods=["POST", "GET"])
def mp_webhook():
    try:
        payment_id = request.args.get("id") or request.args.get("data.id")
        body = None
        if not payment_id and request.is_json:
            body = request.get_json(silent=True) or {}
            payment_id = (body.get("data") or {}).get("id") or body.get("id")

        if not payment_id:
            return jsonify({"ok": False, "error": "missing payment_id"}), 400

        info = mp_get_payment(str(payment_id))
        status = info.get("status")

        if status in ("approved", "accredited", "paid"):
            changed = approve_transaction_by_mp_id(str(payment_id))
            if changed:
                tx = get_transaction_by_mp_id(str(payment_id))
                if tx:
                    amount = float(tx["amount"])
                    user_db_id = tx["user_id"]
                    # buscar telegram_id
                    conn = sqlite3.connect(DB_PATH)
                    cur = conn.cursor()
                    cur.execute("SELECT telegram_id FROM users WHERE id = ?", (user_db_id,))
                    row = cur.fetchone()
                    conn.close()
                    if row:
                        telegram_id = int(row[0])
                        credit_balance(telegram_id, amount)
                        # notificar usuário
                        try:
                            bot.send_message(
                                telegram_id,
                                f"✅ <b>PIX Aprovado!</b>\n\n💸 Valor: R$ {amount:.2f}\n🔐 Saldo adicionado na sua conta!",
                                parse_mode="HTML"
                            )
                        except Exception:
                            pass

        return jsonify({"ok": True, "status": status}), 200
    except Exception as e:
        print("ERRO NO WEBHOOK:", e)
        return jsonify({"ok": False, "error": str(e)}), 500

# -------------------------
# Run: Flask thread + Telebot polling
# -------------------------
def run_flask():
    app.run(host="0.0.0.0", port=8000)

if __name__ == "__main__":
    print(f"🤖 Iniciando {STORE_NAME}...")
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    bot.infinity_polling(timeout=60, long_polling_timeout=60)
