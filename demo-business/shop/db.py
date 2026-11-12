"""Connections and the idempotent setup the app runs on each start.

Setup applies schema.sql, then merchant_setup.sql (a verbatim copy of SettleIQ's merchant-side
script) once per schema, then the read-only role SettleIQ's merchant-postgres source uses. Role
passwords come from the environment on every start, so rotating one is a redeploy.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from shop.config import Settings

SQL = Path(__file__).with_name("sql")
READER = "settleiq_reader"
WRITER = "settleiq_merchant_writer"
CREATE_WRITER = ("CREATE ROLE settleiq_merchant_writer LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                 "NOINHERIT;")
# What SettleIQ's merchant-postgres source may read. The demo controls are not business data.
BUSINESS_TABLES = ("accounts", "plans", "orders", "payments", "subscriptions", "invoices")


@contextmanager
def connect(settings: Settings) -> Iterator[psycopg.Connection]:
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        yield conn


def _role_exists(conn: psycopg.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (name,)).fetchone() is not None


def _password(conn: psycopg.Connection, role: str, password: str) -> None:
    value = sql.Literal(password) if password else sql.SQL("NULL")
    conn.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(role), value))


def setup(conn: psycopg.Connection, settings: Settings) -> None:
    conn.execute("SELECT pg_advisory_xact_lock(hashtext('kitebase-setup'))")
    conn.execute((SQL / "schema.sql").read_text(encoding="utf-8"))
    view = conn.execute("SELECT to_regclass('settleiq_payments') AS v").fetchone()["v"]
    if view is None:
        script = (SQL / "merchant_setup.sql").read_text(encoding="utf-8")
        if _role_exists(conn, WRITER):
            # Roles belong to the server, not the database: one an earlier database created is
            # reused, and only its CREATE is skipped. Its grants here come from the script.
            if CREATE_WRITER not in script:
                raise RuntimeError("merchant_setup.sql no longer creates the writer role as "
                                   "expected; update CREATE_WRITER.")
            script = script.replace(CREATE_WRITER, "")
        conn.execute(script)
    if not _role_exists(conn, READER):
        conn.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                             "NOINHERIT").format(sql.Identifier(READER)))
    schema = conn.execute("SELECT current_schema() AS s").fetchone()["s"]
    conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
        sql.Identifier(schema), sql.Identifier(READER)))
    conn.execute(sql.SQL("GRANT SELECT ON {} TO {}").format(
        sql.SQL(", ").join(sql.Identifier(t) for t in BUSINESS_TABLES), sql.Identifier(READER)))
    for role in (READER, WRITER):
        conn.execute(sql.SQL("ALTER ROLE {} SET search_path = {}").format(
            sql.Identifier(role), sql.Identifier(schema)))
    _password(conn, READER, settings.reader_password)
    _password(conn, WRITER, settings.writer_password)
