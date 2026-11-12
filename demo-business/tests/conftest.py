"""Fixtures: a fresh schema per test in the PostgreSQL that DEMO_TEST_DATABASE_URL names, and a
fake PayPal that keeps orders and captures in memory.

The URL must be an owner role that can create roles (the setup creates SettleIQ's two). Tests
are skipped without it, unless REQUIRE_DB_TESTS=1, as in CI.
"""

import dataclasses
import os
import uuid
from decimal import Decimal
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import psycopg
import pytest
from fastapi.testclient import TestClient

from shop.config import SANDBOX_API, Settings
from shop.main import create_app
from shop.paypal import PayPalError

SCHEMA = "kitebase_test"
READER_PASSWORD = "reader-test-password"
WRITER_PASSWORD = "writer-test-password"


class FakePayPal:
    """PayPal Orders v2 as Kitebase uses it. It accepts duplicate invoice IDs, as the demo's
    Sandbox account must, unless `refuse_duplicate_invoices` is set."""

    configured = True

    def __init__(self) -> None:
        self.orders: dict[str, dict[str, Any]] = {}
        self.refuse_duplicate_invoices = False

    def create_order(self, order_ref: str, payment_id: str, amount: Decimal, currency: str,
                     description: str) -> str:
        if self.refuse_duplicate_invoices and any(
                o["invoice_id"] == order_ref and o["captures"] for o in self.orders.values()):
            raise PayPalError("PayPal answered 422 (DUPLICATE_INVOICE_ID).", "DUPLICATE_INVOICE_ID")
        order_id = f"PPO-{uuid.uuid4().hex[:12].upper()}"
        self.orders[order_id] = {"invoice_id": order_ref, "custom_id": payment_id,
                                 "amount": f"{amount:.2f}", "currency": currency,
                                 "status": "CREATED", "captures": []}
        return order_id

    def capture_order(self, paypal_order_id: str) -> dict[str, str]:
        order = self.orders[paypal_order_id]
        if not order["captures"]:
            order["status"] = "COMPLETED"
            order["captures"].append({"id": f"CAP-{uuid.uuid4().hex[:12].upper()}",
                                      "status": "COMPLETED"})
        return {"capture_id": order["captures"][0]["id"], "status": "COMPLETED"}

    def get_order(self, paypal_order_id: str) -> dict[str, Any]:
        order = self.orders[paypal_order_id]
        amount = {"value": order["amount"], "currency_code": order["currency"]}
        return {"id": paypal_order_id, "status": order["status"], "purchase_units": [{
            "invoice_id": order["invoice_id"],
            "payments": {"captures": [{**c, "amount": amount} for c in order["captures"]]},
        }]}

    def captures_for(self, order_ref: str) -> list[str]:
        return [c["id"] for o in self.orders.values() if o["invoice_id"] == order_ref
                for c in o["captures"]]


def _with_schema(url: str) -> str:
    parts = urlsplit(url)
    option = quote(f"-csearch_path={SCHEMA}")
    query = f"{parts.query}&options={option}" if parts.query else f"options={option}"
    return urlunsplit(parts._replace(query=query))


def as_role(url: str, role: str, password: str) -> str:
    """The same server and database, connected as one of SettleIQ's roles."""
    parts = urlsplit(url)
    host = parts.hostname or "127.0.0.1"
    netloc = f"{role}:{quote(password)}@{host}" + (f":{parts.port}" if parts.port else "")
    return urlunsplit(parts._replace(netloc=netloc, query=""))


@pytest.fixture
def database_url() -> str:
    url = os.environ.get("DEMO_TEST_DATABASE_URL")
    if not url:
        if os.environ.get("REQUIRE_DB_TESTS") == "1":
            pytest.fail("DEMO_TEST_DATABASE_URL is not set")
        pytest.skip("DEMO_TEST_DATABASE_URL is not set")
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        conn.execute(f"CREATE SCHEMA {SCHEMA}")
    return _with_schema(url)


@pytest.fixture
def settings(database_url: str) -> Settings:
    return Settings(
        database_url=database_url, merchant_id="kitebase", paypal_client_id="test-client",
        paypal_client_secret="test-secret", paypal_api_base=SANDBOX_API,
        reader_password=READER_PASSWORD, writer_password=WRITER_PASSWORD,
        settleiq_url="https://settleiq.example",
    )


@pytest.fixture
def paypal() -> FakePayPal:
    return FakePayPal()


@pytest.fixture
def make_client(settings: Settings, paypal: FakePayPal):
    clients = []

    def make(**overrides: Any) -> TestClient:
        client = TestClient(create_app(dataclasses.replace(settings, **overrides), paypal))
        clients.append(client.__enter__())
        return client

    yield make
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def client(make_client) -> TestClient:
    return make_client()


@pytest.fixture
def sql(settings: Settings):
    """Runs one statement as the owner and returns its rows."""

    def run(statement: str, params: tuple = ()) -> list[dict[str, Any]]:
        with psycopg.connect(settings.database_url, row_factory=psycopg.rows.dict_row) as conn:
            cursor = conn.execute(statement, params)
            return cursor.fetchall() if cursor.description else []

    return run
