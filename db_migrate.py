# db_migrate.py
# Script de migração segura do SQLite para a loja com admins, relatórios, produtos, acessos e transações

import sqlite3

DB_PATH = "store.db"  # Ajuste se usar outro nome

def run_migrations():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    print("\n=== INICIANDO MIGRAÇÕES DO BANCO ===")

    # -----------------------------
    # USERS
    # -----------------------------
    print("-> Garantindo tabela: users")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER UNIQUE NOT NULL,
        username TEXT,
        first_name TEXT,
        last_name TEXT
    )
    """)

    # -----------------------------
    # WALLET
    # -----------------------------
    print("-> Garantindo tabela: wallet")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS wallet (
        user_id INTEGER PRIMARY KEY,
        balance REAL NOT NULL DEFAULT 0,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )
    """)

    # -----------------------------
    # PRODUCTS
    # -----------------------------
    print("-> Garantindo tabela: products")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        price REAL NOT NULL,
        stock INTEGER DEFAULT 0,
        active INTEGER DEFAULT 1
    )
    """)

    # -----------------------------
    # PRODUCT_ACCESS
    # -----------------------------
    print("-> Garantindo tabela: product_access")
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

    # -----------------------------
    # TRANSACTIONS
    # -----------------------------
    print("-> Garantindo tabela: transactions")
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

    # -----------------------------
    # ADMINS
    # -----------------------------
    print("-> Garantindo tabela: admins")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS admins (
        telegram_id INTEGER PRIMARY KEY,
        nome TEXT,
        senha TEXT,
        nivel INTEGER NOT NULL DEFAULT 1
    )
    """)

    # -----------------------------
    # BANNED USERS
    # -----------------------------
    print("-> Garantindo tabela: banned_users")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS banned_users (
        telegram_id INTEGER PRIMARY KEY
    )
    """)

    # -----------------------------
    # SALES (para relatórios)
    # -----------------------------
    print("-> Garantindo tabela: sales")
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

    # -----------------------------
    # COLUNAS DE MIGRAÇÃO EXTRA
    # (caso versões antigas não tivessem)
    # -----------------------------

    def add_column(table, column, type_def):
        try:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {type_def}")
            print(f"-> Coluna adicionada: {table}.{column}")
        except sqlite3.OperationalError:
            pass  # coluna já existe

    print("-> Garantindo colunas extras opcionais...")
    add_column("products", "active", "INTEGER DEFAULT 1")
    add_column("transactions", "approved_at", "TEXT")

    # Finalizar
    conn.commit()
    conn.close()

    print("\n=== MIGRAÇÕES FINALIZADAS COM SUCESSO ===")


if __name__ == "__main__":
    run_migrations()
