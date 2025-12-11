# db.py
# Camada de acesso ao banco (SQLite) compatível com bot.py
# - Preserva tabelas antigas
# - Cria novas tabelas necessárias
# - Expõe funções usadas por bot.py (ensure_user, get_balance, add_transaction, etc.)

import sqlite3
import json
from typing import Optional, List, Dict

DB_PATH = "store.db"  # ajuste se usar outro arquivo

def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# -------------------------
# MIGRAÇÃO / CRIAÇÃO DE TABELAS
# -------------------------
def migrate():
    """
    Cria tabelas que faltam sem apagar as existentes.
    Execute no start do bot (ou chame manualmente).
    """
    conn = _conn()
    cur = conn.cursor()

    # USERS (compatível com versões anteriores)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER UNIQUE NOT NULL,
        username TEXT,
        first_name TEXT,
        last_name TEXT
    )
    """)

    # WALLET / CARTEIRA
    cur.execute("""
    CREATE TABLE IF NOT EXISTS wallet (
        user_id INTEGER PRIMARY KEY,
        balance REAL NOT NULL DEFAULT 0,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )
    """)

    # PRODUCTS (mantemos compatibilidade com nome 'products')
    cur.execute("""
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        price REAL NOT NULL,
        stock INTEGER DEFAULT 0,
        active INTEGER DEFAULT 1
    )
    """)

    # PRODUCT_ACCESS (acessos/credenciais)
    # Colunas usadas anteriormente: product_id, login, senha, vendido
    cur.execute("""
    CREATE TABLE IF NOT EXISTS product_access (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER NOT NULL,
        login TEXT NOT NULL,
        senha TEXT NOT NULL,
        vendido INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY (product_id) REFERENCES products(id)
    )
    """)

    # TRANSACTIONS (mp / recargas)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        mp_id TEXT,
        amount REAL NOT NULL,
        status TEXT NOT NULL,
        description TEXT,
        raw_json TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        approved_at TEXT,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )
    """)

    # ADMINS persistentes (telegram_id, nome, senha, nivel)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS admins (
        telegram_id INTEGER PRIMARY KEY,
        nome TEXT,
        senha TEXT,
        nivel INTEGER NOT NULL DEFAULT 1
    )
    """)

    # BANNED USERS persistente
    cur.execute("""
    CREATE TABLE IF NOT EXISTS banned_users (
        telegram_id INTEGER PRIMARY KEY
    )
    """)

    # SALES - tabela para relatórios (registro simplificado)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sales (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        product_id INTEGER,
        amount REAL,
        quantity INTEGER DEFAULT 1,
        date TEXT DEFAULT (datetime('now'))
    )
    """)

    conn.commit()
    conn.close()

# Execute migração automaticamente ao importar
try:
    migrate()
except Exception as e:
    print("Erro ao migrar DB:", e)

# -------------------------
# Usuários & Carteira
# -------------------------
def ensure_user(telegram_id: int, username: Optional[str], first_name: Optional[str], last_name: Optional[str]) -> int:
    """
    Garante que o usuário exista; atualiza campos básicos se necessário.
    Retorna o user.id interno (inteiro).
    """
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE telegram_id = ?", (telegram_id,))
    row = cur.fetchone()

    if not row:
        cur.execute(
            "INSERT INTO users (telegram_id, username, first_name, last_name) VALUES (?, ?, ?, ?)",
            (telegram_id, username, first_name, last_name)
        )
        user_id = cur.lastrowid
        # cria carteira
        cur.execute("INSERT OR IGNORE INTO wallet (user_id, balance) VALUES (?, 0)", (user_id,))
    else:
        user_id = row["id"]
        # atualiza parcialmente (COALESCE sem apagar)
        cur.execute("""
            UPDATE users SET
                username = COALESCE(?, username),
                first_name = COALESCE(?, first_name),
                last_name = COALESCE(?, last_name)
            WHERE id = ?
        """, (username, first_name, last_name, user_id))

        # garante carteira existe
        cur.execute("INSERT OR IGNORE INTO wallet (user_id, balance) VALUES (?, 0)", (user_id,))

    conn.commit()
    conn.close()
    return user_id

def get_user_by_telegram(telegram_id: int) -> Optional[Dict]:
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None

def get_user_by_id(user_id: int) -> Optional[Dict]:
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None

def get_balance(telegram_id: int) -> float:
    conn = _conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT w.balance
        FROM wallet w
        JOIN users u ON u.id = w.user_id
        WHERE u.telegram_id = ?
    """, (telegram_id,))
    row = cur.fetchone()
    conn.close()
    return float(row["balance"]) if row else 0.0

def debit_balance(telegram_id: int, amount: float) -> None:
    conn = _conn()
    cur = conn.cursor()
    cur.execute("""
        UPDATE wallet
        SET balance = balance - ?
        WHERE user_id = (SELECT id FROM users WHERE telegram_id = ?)
    """, (amount, telegram_id))
    conn.commit()
    conn.close()

def credit_balance(telegram_id: int, amount: float) -> None:
    conn = _conn()
    cur = conn.cursor()
    cur.execute("""
        UPDATE wallet
        SET balance = balance + ?
        WHERE user_id = (SELECT id FROM users WHERE telegram_id = ?)
    """, (amount, telegram_id))
    conn.commit()
    conn.close()

# -------------------------
# Transações (MP / PIX)
# -------------------------
def add_transaction(user_id: int, mp_id: str, amount: float, status: str, description: Optional[str]=None, raw_json: Optional[dict]=None) -> int:
    conn = _conn()
    cur = conn.cursor()
    raw_text = json.dumps(raw_json, ensure_ascii=False) if raw_json is not None else None
    cur.execute("""
        INSERT INTO transactions (user_id, mp_id, amount, status, description, raw_json)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (user_id, mp_id, amount, status, description, raw_text))
    tx_id = cur.lastrowid
    conn.commit()
    conn.close()
    return tx_id

def approve_transaction_by_mp_id(mp_id: str) -> bool:
    """
    Marca transação aprovada/creditada. Retorna True se mudou algo.
    """
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT id, status FROM transactions WHERE mp_id = ?", (mp_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return False
    if row["status"] in ("approved", "aprovado", "accredited", "paid"):
        conn.close()
        return False
    cur.execute("UPDATE transactions SET status = 'approved', approved_at = datetime('now') WHERE mp_id = ?", (mp_id,))
    conn.commit()
    changed = cur.rowcount > 0
    conn.close()
    return changed

def get_transaction_by_mp_id(mp_id: str) -> Optional[Dict]:
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM transactions WHERE mp_id = ?", (mp_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None

def get_approved_history(telegram_id: int, limit: int = 20) -> List[Dict]:
    """
    Retorna histórico de transações aprovadas (join com users).
    """
    conn = _conn()
    cur = conn.cursor()
    # considera vários status compatíveis com aprovações
    cur.execute("""
        SELECT t.amount, t.approved_at, t.mp_id, t.created_at
        FROM transactions t
        JOIN users u ON u.id = t.user_id
        WHERE u.telegram_id = ? AND t.status IN ('approved','aprovado','accredited','paid')
        ORDER BY t.approved_at DESC, t.created_at DESC
        LIMIT ?
    """, (telegram_id, limit))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]

# -------------------------
# Produtos e acessos
# -------------------------
def list_products() -> List[Dict]:
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT id, name, price, stock, active FROM products WHERE active=1")
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_product(product_id: int) -> Optional[Dict]:
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT id, name, price, stock, active FROM products WHERE id = ?", (product_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None

def get_available_access(product_id: int) -> Optional[Dict]:
    """
    Retorna o primeiro acesso não vendido como dicionário com keys:
    id, login, password
    (mapeia coluna 'senha' -> 'password' para compatibilidade com bot.py)
    """
    conn = _conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, login, senha FROM product_access
        WHERE product_id = ? AND vendido = 0
        LIMIT 1
    """, (product_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return {"id": row["id"], "login": row["login"], "password": row["senha"]}

def mark_access_sold(access_id: int) -> None:
    conn = _conn()
    cur = conn.cursor()
    cur.execute("UPDATE product_access SET vendido = 1 WHERE id = ?", (access_id,))
    conn.commit()
    conn.close()

def add_product(name: str, price: float, stock: int = 0) -> int:
    conn = _conn()
    cur = conn.cursor()
    cur.execute("INSERT INTO products (name, price, stock) VALUES (?, ?, ?)", (name, price, stock))
    pid = cur.lastrowid
    conn.commit()
    conn.close()
    return pid

def add_product_access(product_id: int, login: str, senha: str) -> int:
    conn = _conn()
    cur = conn.cursor()
    cur.execute("INSERT INTO product_access (product_id, login, senha, vendido) VALUES (?, ?, ?, 0)", (product_id, login, senha))
    aid = cur.lastrowid
    conn.commit()
    conn.close()
    return aid

# -------------------------
# Admins (persistentes) - helpers para bot
# -------------------------
def is_admin_level(telegram_id: int, senha: Optional[str] = None, min_level: int = 1) -> bool:
    """
    Verifica se telegram_id é admin com senha (se senha for passada) e nivel >= min_level.
    Usado por bot para autenticar /admin <senha>.
    """
    conn = _conn()
    cur = conn.cursor()
    if senha is None:
        cur.execute("SELECT nivel FROM admins WHERE telegram_id = ?", (telegram_id,))
        row = cur.fetchone()
    else:
        cur.execute("SELECT nivel FROM admins WHERE telegram_id = ? AND senha = ?", (telegram_id, senha))
        row = cur.fetchone()
    conn.close()
    if not row:
        return False
    try:
        return int(row["nivel"]) >= int(min_level)
    except Exception:
        return False

def add_admin_db(telegram_id: int, nome: Optional[str], senha: str, nivel: int = 1) -> None:
    conn = _conn()
    cur = conn.cursor()
    cur.execute("REPLACE INTO admins (telegram_id, nome, senha, nivel) VALUES (?, ?, ?, ?)", (telegram_id, nome, senha, nivel))
    conn.commit()
    conn.close()

def remove_admin_db(telegram_id: int) -> None:
    conn = _conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM admins WHERE telegram_id = ?", (telegram_id,))
    conn.commit()
    conn.close()

def list_admins_db() -> List[Dict]:
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT telegram_id, nome, nivel FROM admins")
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]

# -------------------------
# Banimentos persistentes
# -------------------------
def ban_user_db(telegram_id: int) -> None:
    conn = _conn()
    cur = conn.cursor()
    cur.execute("REPLACE INTO banned_users (telegram_id) VALUES (?)", (telegram_id,))
    conn.commit()
    conn.close()

def unban_user_db(telegram_id: int) -> None:
    conn = _conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM banned_users WHERE telegram_id = ?", (telegram_id,))
    conn.commit()
    conn.close()

def is_banned_db(telegram_id: int) -> bool:
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM banned_users WHERE telegram_id = ?", (telegram_id,))
    row = cur.fetchone()
    conn.close()
    return row is not None

# -------------------------
# Relatórios (usado por /report)
# -------------------------
def get_sales_report(period: str = "total") -> Dict:
    """
    period: 'total', 'daily', 'weekly', 'monthly'
    Retorna dict: {count: int, total: float}
    Usa tabela transactions (aprovadas) como fonte primária e fallback em sales.
    """
    conn = _conn()
    cur = conn.cursor()

    approved_statuses = ("approved", "aprovado", "accredited", "paid")

    if period == "total":
        cur.execute(f"SELECT COUNT(*), COALESCE(SUM(amount),0) FROM transactions WHERE status IN ({','.join(['?']*len(approved_statuses))})", approved_statuses)
        cnt, total = cur.fetchone()
        conn.close()
        return {"count": int(cnt or 0), "total": float(total or 0.0)}

    if period == "daily":
        cur.execute(f"SELECT COUNT(*), COALESCE(SUM(amount),0) FROM transactions WHERE status IN ({','.join(['?']*len(approved_statuses))}) AND DATE(approved_at)=DATE('now')", approved_statuses)
        cnt, total = cur.fetchone()
        conn.close()
        return {"count": int(cnt or 0), "total": float(total or 0.0)}

    if period == "weekly":
        cur.execute(f"SELECT COUNT(*), COALESCE(SUM(amount),0) FROM transactions WHERE status IN ({','.join(['?']*len(approved_statuses))}) AND DATE(approved_at) >= DATE('now','-6 days')", approved_statuses)
        cnt, total = cur.fetchone()
        conn.close()
        return {"count": int(cnt or 0), "total": float(total or 0.0)}

    if period == "monthly":
        cur.execute(f"SELECT COUNT(*), COALESCE(SUM(amount),0) FROM transactions WHERE status IN ({','.join(['?']*len(approved_statuses))}) AND strftime('%Y-%m', approved_at) = strftime('%Y-%m','now')", approved_statuses)
        cnt, total = cur.fetchone()
        conn.close()
        return {"count": int(cnt or 0), "total": float(total or 0.0)}

    # fallback
    conn.close()
    return {"count": 0, "total": 0.0}

# -------------------------
# Função utilitária: gravar venda (sales) quando necessário
# -------------------------
def register_sale(user_id: int, product_id: int, price: float, quantity: int = 1) -> int:
    amount = float(price) * int(quantity)
    conn = _conn()
    cur = conn.cursor()
    cur.execute("INSERT INTO sales (user_id, product_id, amount, quantity) VALUES (?, ?, ?, ?)", (user_id, product_id, amount, quantity))
    sid = cur.lastrowid
    conn.commit()
    conn.close()
    return sid

# -------------------------
# UTILIDADES
# -------------------------
def row_to_dict(row):
    return dict(row) if row else None

# -------------------------
# Fim do db.py
# -------------------------
