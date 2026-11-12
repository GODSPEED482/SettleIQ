"""Compatibility with SettleIQ's connectors (task 6.11): the merchant-postgres read role and the
internal-merchant writer role see exactly what each connector needs and nothing more."""

import dataclasses
from pathlib import Path

import psycopg
import pytest
from conftest import READER_PASSWORD, WRITER_PASSWORD, as_role

from shop import db

REPO = Path(__file__).resolve().parents[2]
BACKEND_SETUP = REPO / "backend/app/connectors/internal_merchant/merchant_setup.sql"
SHOP_SETUP = Path(db.SQL / "merchant_setup.sql")


def connect_as(settings, role, password):
    return psycopg.connect(as_role(settings.database_url, role, password), autocommit=True)


def test_merchant_setup_is_a_verbatim_copy_of_settleiqs():
    if not BACKEND_SETUP.exists():
        pytest.skip("not in the SettleIQ repository")
    assert SHOP_SETUP.read_bytes() == BACKEND_SETUP.read_bytes()


def test_the_writer_view_has_the_columns_the_adapter_reads(client, settings):
    with connect_as(settings, "settleiq_merchant_writer", WRITER_PASSWORD) as conn:
        cursor = conn.execute("SELECT * FROM settleiq_payments LIMIT 0")
        assert [c.name for c in cursor.description] == [
            "payment_id", "merchant_id", "status", "amount", "currency", "order_ref"]
        conn.execute("SELECT * FROM settleiq_action_records LIMIT 0")


def test_the_writer_may_change_status_only(client, settings):
    client.post("/api/checkout", json={"plan_id": "pro-monthly", "email": "w@example.com"})
    with connect_as(settings, "settleiq_merchant_writer", WRITER_PASSWORD) as conn:
        assert conn.execute("UPDATE settleiq_payments SET status = 'COMPLETED'").rowcount == 1
        for statement in ("UPDATE settleiq_payments SET amount = 1",
                          "DELETE FROM settleiq_payments",
                          "SELECT * FROM payments",
                          "SELECT * FROM orders",
                          "SELECT * FROM demo_meta"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement)


def test_the_reader_reads_the_business_tables_and_writes_nothing(client, settings):
    with connect_as(settings, "settleiq_reader", READER_PASSWORD) as conn:
        for table in db.BUSINESS_TABLES:
            conn.execute(f"SELECT * FROM {table} LIMIT 1")
        for statement in ("SELECT * FROM demo_meta",
                          "SELECT * FROM checkout_attempts",
                          "UPDATE payments SET status = 'COMPLETED'",
                          "INSERT INTO plans VALUES ('x', 'x', 'ADDON', NULL, 1, 'USD')",
                          "CREATE TABLE intruder (id int)"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement)


def test_payments_have_one_primary_key_and_settleiqs_status_words(client, sql):
    key = sql("""
        SELECT a.attname FROM pg_index i
        JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
        WHERE i.indrelid = 'payments'::regclass AND i.indisprimary
    """)
    assert [k["attname"] for k in key] == ["payment_id"]
    client.post("/api/checkout", json={"plan_id": "pro-monthly", "email": "k@example.com"})
    with pytest.raises(psycopg.errors.CheckViolation):
        sql("UPDATE payments SET status = 'paid'")


def test_setup_is_idempotent_and_rotates_passwords(client, settings):
    with db.connect(settings) as conn:
        db.setup(conn, settings)
        db.setup(conn, dataclasses.replace(settings, reader_password="rotated-password"))
    with connect_as(settings, "settleiq_reader", "rotated-password") as conn:
        conn.execute("SELECT 1 FROM payments LIMIT 1")
    with pytest.raises(psycopg.OperationalError):
        connect_as(settings, "settleiq_reader", READER_PASSWORD)


def test_a_second_schema_reuses_the_writer_role_and_leaves_the_first_intact(client, settings):
    other = settings.database_url.replace("kitebase_test", "kitebase_other")
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS kitebase_other CASCADE")
        conn.execute("CREATE SCHEMA kitebase_other")
    try:
        with db.connect(dataclasses.replace(settings, database_url=other)) as conn:
            db.setup(conn, settings)
            assert conn.execute("SELECT to_regclass('settleiq_payments') AS v").fetchone()["v"]
        with connect_as(settings, "settleiq_merchant_writer", WRITER_PASSWORD) as conn:
            conn.execute("SELECT * FROM kitebase_test.settleiq_payments LIMIT 1")
            conn.execute("SELECT * FROM kitebase_other.settleiq_payments LIMIT 1")
    finally:
        with psycopg.connect(settings.database_url, autocommit=True) as conn:
            conn.execute("DROP SCHEMA kitebase_other CASCADE")
