import os
import sqlite3
from config import DATABASE_PATH

def connect():
    os.makedirs(os.path.dirname(DATABASE_PATH), exist_ok=True)
    db = sqlite3.connect(DATABASE_PATH, timeout=20)
    db.row_factory = sqlite3.Row
    return db

def init_db():
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            username TEXT,
            balance REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            service_id TEXT,
            link TEXT,
            quantity INTEGER,
            cost REAL NOT NULL DEFAULT 0,
            sale_price REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending_payment',
            provider_order_id TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            order_id INTEGER,
            amount REAL NOT NULL,
            provider TEXT,
            external_id TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        INSERT OR IGNORE INTO settings (key, value)
        VALUES ('profit_percent', '30');
        """)

def get_profit_percent():
    with connect() as db:
        row = db.execute(
            "SELECT value FROM settings WHERE key = 'profit_percent'"
        ).fetchone()
        return float(row["value"]) if row else 30.0

def set_profit_percent(percent):
    if percent < 0 or percent > 1000:
        raise ValueError("A porcentagem deve estar entre 0 e 1000.")

    with connect() as db:
        db.execute("""
            INSERT INTO settings (key, value)
            VALUES ('profit_percent', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """, (str(percent),))

def save_user(telegram_id, username):
    with connect() as db:
        db.execute("""
            INSERT INTO users (telegram_id, username)
            VALUES (?, ?)
            ON CONFLICT(telegram_id)
            DO UPDATE SET username = excluded.username
        """, (telegram_id, username or ""))



def consultar_saldo_usuario(telegram_id):
    """Retorna o saldo da conta do próprio usuário."""
    with connect() as db:
        row = db.execute(
            "SELECT balance FROM users WHERE telegram_id = ?",
            (int(telegram_id),),
        ).fetchone()

        if row is None:
            db.execute(
                "INSERT INTO users (telegram_id, username, balance) "
                "VALUES (?, '', 0)",
                (int(telegram_id),),
            )
            return 0.0

        return float(row["balance"])

def criar_pedido_local(telegram_id, service_id, link, quantity, cost, sale_price, comments=""):
    with connect() as db:
        cursor = db.execute(
            """
            INSERT INTO orders (
                telegram_id, service_id, link, quantity,
                cost, sale_price, status, comments
            )
            VALUES (?, ?, ?, ?, ?, ?, 'pending_payment', ?)
            """,
            (
                int(telegram_id), str(service_id), str(link),
                int(quantity), float(cost), float(sale_price), str(comments or "")
            ),
        )
        return cursor.lastrowid
def criar_pagamento_local(telegram_id, order_id, amount, provider):
    with connect() as db:
        cursor = db.execute(
            """
            INSERT INTO payments (
                telegram_id, order_id, amount, provider, status
            )
            VALUES (?, ?, ?, ?, 'pending')
            """,
            (int(telegram_id), int(order_id), float(amount), str(provider)),
        )
        return cursor.lastrowid


def associar_pagamento_externo(payment_id, external_id):
    with connect() as db:
        db.execute(
            """
            UPDATE payments
            SET external_id = ?
            WHERE id = ? AND status = 'pending'
            """,
            (str(external_id), int(payment_id)),
        )


def buscar_pagamento(payment_id):
    with connect() as db:
        return db.execute(
            "SELECT * FROM payments WHERE id = ?",
            (int(payment_id),),
        ).fetchone()


def marcar_pagamento_aprovado(payment_id, external_id):
    with connect() as db:
        cursor = db.execute(
            """
            UPDATE payments
            SET status = 'approved'
            WHERE id = ?
              AND external_id = ?
              AND status = 'pending'
            """,
            (int(payment_id), str(external_id)),
        )
        return cursor.rowcount == 1


def atualizar_status_pedido(order_id, status, provider_order_id=None):
    with connect() as db:
        db.execute(
            """
            UPDATE orders
            SET status = ?,
                provider_order_id = COALESCE(?, provider_order_id)
            WHERE id = ?
            """,
            (str(status), provider_order_id, int(order_id)),
        )


def creditar_recarga_aprovada(payment_id, telegram_id, external_id):
    """Credita uma recarga pendente uma única vez, dentro de uma transação."""
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        pagamento = db.execute(
            """
            SELECT telegram_id, amount, external_id, status, order_id
            FROM payments
            WHERE id = ?
            """,
            (int(payment_id),),
        ).fetchone()

        if not pagamento:
            return False
        if int(pagamento["telegram_id"]) != int(telegram_id):
            return False
        if pagamento["order_id"] is not None:
            return False
        if pagamento["status"] != "pending":
            return False
        if str(pagamento["external_id"] or "") != str(external_id):
            return False

        db.execute(
            """
            INSERT OR IGNORE INTO users (telegram_id, username, balance)
            VALUES (?, '', 0)
            """,
            (int(telegram_id),),
        )

        atualizado = db.execute(
            """
            UPDATE payments
            SET status = 'approved'
            WHERE id = ?
              AND telegram_id = ?
              AND order_id IS NULL
              AND external_id = ?
              AND status = 'pending'
            """,
            (int(payment_id), int(telegram_id), str(external_id)),
        )

        if atualizado.rowcount != 1:
            return False

        db.execute(
            """
            UPDATE users
            SET balance = ROUND(balance + ?, 2)
            WHERE telegram_id = ?
            """,
            (float(pagamento["amount"]), int(telegram_id)),
        )
        return True


def criar_recarga_local(telegram_id, amount, provider="mercadopago"):
    """Registra uma recarga pendente, sem associá-la a um pedido de serviço."""
    valor = round(float(amount), 2)
    if valor < 5 or valor > 500:
        raise ValueError("A recarga deve estar entre R$ 5,00 e R$ 500,00.")

    with connect() as db:
        cursor = db.execute(
            """
            INSERT INTO payments (
                telegram_id, order_id, amount, provider, status
            )
            VALUES (?, NULL, ?, ?, 'pending')
            """,
            (int(telegram_id), valor, str(provider)),
        )
        return cursor.lastrowid


def debitar_saldo_usuario(telegram_id, valor):
    """Debita saldo atomicamente, impedindo saldo negativo."""
    valor = round(float(valor), 2)
    if valor <= 0:
        return False

    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        cursor = db.execute(
            """
            UPDATE users
            SET balance = ROUND(balance - ?, 2)
            WHERE telegram_id = ?
              AND balance >= ?
            """,
            (valor, int(telegram_id), valor),
        )
        return cursor.rowcount == 1


def criar_pedido_com_saldo(telegram_id, service_id, link, quantity, cost, sale_price, comments=""):
    """Debita a carteira e cria o pedido atomicamente."""
    valor = round(float(sale_price), 2)
    if valor <= 0:
        return None, "valor_invalido"
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        usuario = db.execute(
            "SELECT balance FROM users WHERE telegram_id = ?",
            (int(telegram_id),),
        ).fetchone()
        if usuario is None:
            return None, "saldo_insuficiente"
        saldo = round(float(usuario["balance"]), 2)
        if saldo < valor:
            return None, "saldo_insuficiente"
        debito = db.execute(
            """
            UPDATE users
            SET balance = ROUND(balance - ?, 2)
            WHERE telegram_id = ? AND balance >= ?
            """,
            (valor, int(telegram_id), valor),
        )
        if debito.rowcount != 1:
            return None, "saldo_insuficiente"
        cursor = db.execute(
            """
            INSERT INTO orders (
                telegram_id, service_id, link, quantity,
                cost, sale_price, status, comments
            )
            VALUES (?, ?, ?, ?, ?, ?, 'submitting', ?)
            """,
            (
                int(telegram_id), str(service_id), str(link),
                int(quantity), float(cost), valor, str(comments or ""),
            ),
        )
        return cursor.lastrowid, "ok"
