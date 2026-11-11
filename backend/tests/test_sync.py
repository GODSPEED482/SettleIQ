"""Merchant PostgreSQL record reads (task 3.5, AC-REC-01, AC-REC-02)."""

import json
import os
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.db import scoped_tx
from tests.support import grant_set
from tests.test_semantics_store import Member
from tests.test_sources import create_ok

SECRET = "env:SOURCE_SECRET_TEST_MERCHANT"


def operator(name: str = "T") -> Member:
    member = Member(name)
    grant_set(member.tenant, member.principal.id, "integration_admin")
    return member

FIXTURE = """
CREATE TABLE {schema}.customers (
    id integer PRIMARY KEY,
    email text NOT NULL UNIQUE
);
CREATE TABLE {schema}.payments (
    payment_id text PRIMARY KEY,
    customer_id integer REFERENCES {schema}.customers (id),
    order_ref text,
    amount numeric(20, 6) NOT NULL,
    currency char(3) NOT NULL,
    status text NOT NULL,
    created_at timestamptz NOT NULL,
    note text
);
CREATE TABLE {schema}.composite_pk (
    left_id text NOT NULL,
    right_id text NOT NULL,
    amount numeric(20, 6) NOT NULL,
    PRIMARY KEY (left_id, right_id)
);
CREATE TABLE {schema}.no_pk (
    label text,
    amount numeric(20, 6)
);
CREATE VIEW {schema}.completed_payments AS
    SELECT payment_id, amount FROM {schema}.payments WHERE status = 'COMPLETED';
INSERT INTO {schema}.customers VALUES (1, 'alice@example.com'), (2, 'bob@example.com');
INSERT INTO {schema}.payments VALUES
    ('PAY-1', 1, 'ORD-1', 10.00, 'USD', 'COMPLETED', '2026-10-03T10:00:00Z', 'secret note'),
    ('PAY-2', 1, 'ORD-2', 25.50, 'USD', 'PENDING', '2026-11-01T10:00:00Z', NULL),
    ('PAY-3', 2, 'ORD-2', 99.99, 'EUR', 'COMPLETED', '2026-11-02T10:00:00Z', NULL),
    ('PAY-4', NULL, 'ORD-4', 4.25, 'USD', 'COMPLETED', '2026-11-05T10:00:00Z', NULL);
INSERT INTO {schema}.composite_pk VALUES ('A', 'B', 1.50), ('C', 'D', 2.50);
INSERT INTO {schema}.no_pk VALUES ('first', 3.00), ('second', 4.00);
"""


@pytest.fixture
def merchant_schema(monkeypatch):
    owner_url = os.environ["MIGRATION_DATABASE_URL"]
    schema = f"merchant_{uuid4().hex[:12]}"
    engine = create_engine(owner_url, poolclass=NullPool)
    with engine.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA {schema}"))
        conn.execute(text(FIXTURE.format(schema=schema)))
    monkeypatch.setenv("SOURCE_SECRET_TEST_MERCHANT", owner_url)
    yield schema
    with engine.begin() as conn:
        conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
    engine.dispose()


@pytest.fixture
def evidence_root(tmp_path, monkeypatch):
    monkeypatch.setenv("RAW_EVIDENCE_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def merchant_source(member: Member, schema: str) -> dict:
    return create_ok(
        member,
        connector_id="merchant-postgres",
        display_name="Merchant DB",
        config={"schema": schema},
        secret_reference=SECRET,
    )


def discover(member: Member, source_id: str):
    return member.request("POST", f"/api/sources/{source_id}/discover")


def sync(member: Member, source_id: str):
    return member.request("POST", f"/api/sources/{source_id}/sync")


def stored_records(member: Member, source_id: str):
    with scoped_tx(member.tenant) as conn:
        return conn.execute(
            text(
                """
                SELECT external_id, record_type, attributes, payload_hash
                FROM source_records WHERE source_id = :source
                ORDER BY record_type, external_id
                """
            ),
            {"source": UUID(source_id)},
        ).all()


@pytest.mark.db
def test_sync_reads_discovered_tables_into_canonical_records(
    merchant_schema, evidence_root
):
    member = operator()
    source = merchant_source(member, merchant_schema)
    assert discover(member, source["id"]).status_code == 200
    response = sync(member, source["id"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["record_count"] == 10
    assert {item["object_name"] for item in body["objects"]} == {
        "customers", "payments", "composite_pk", "no_pk",
    }
    records = stored_records(member, source["id"])
    assert len(records) == 10
    assert {row.record_type for row in records} == {
        f"merchant-postgres:table/{merchant_schema}.customers",
        f"merchant-postgres:table/{merchant_schema}.payments",
        f"merchant-postgres:table/{merchant_schema}.composite_pk",
        f"merchant-postgres:table/{merchant_schema}.no_pk",
    }
    assert not any("completed_payments" in row.record_type for row in records)
    payments = {
        row.external_id: row.attributes
        for row in records
        if row.record_type.endswith(".payments")
    }
    assert set(payments) == {"PAY-1", "PAY-2", "PAY-3", "PAY-4"}
    assert payments["PAY-1"]["amount"] == "10.000000"
    assert isinstance(payments["PAY-1"]["amount"], str)
    assert payments["PAY-1"]["note"] == "secret note"
    assert payments["PAY-1"]["created_at"].startswith("2026-10-03T10:00:00")
    assert payments["PAY-4"]["customer_id"] is None
    composite = [row for row in records if row.record_type.endswith(".composite_pk")]
    expected_ids = {
        json.dumps(["A", "B"], separators=(",", ":")),
        json.dumps(["C", "D"], separators=(",", ":")),
    }
    assert {row.external_id for row in composite} == expected_ids
    keyless = [row for row in records if row.record_type.endswith(".no_pk")]
    assert all(row.external_id.startswith("row:") for row in keyless)


@pytest.mark.db
def test_resync_is_idempotent_until_the_table_changes(merchant_schema, evidence_root):
    member = operator()
    source = merchant_source(member, merchant_schema)
    discover(member, source["id"])
    assert sync(member, source["id"]).status_code == 200
    assert sync(member, source["id"]).status_code == 200
    assert len(stored_records(member, source["id"])) == 10

    engine = create_engine(os.environ["MIGRATION_DATABASE_URL"], poolclass=NullPool)
    with engine.begin() as conn:
        conn.execute(
            text(
                f"UPDATE {merchant_schema}.payments SET status = 'COMPLETED' "
                "WHERE payment_id = 'PAY-2'"
            )
        )
    engine.dispose()

    assert sync(member, source["id"]).status_code == 200
    records = stored_records(member, source["id"])
    # The table response is one evidence body, so every row in the changed table is a new
    # observation; other tables are unchanged and collapse onto their existing records.
    assert len(records) == 14
    assert len([row for row in records if row.external_id == "PAY-2"]) == 2


@pytest.mark.db
def test_sync_requires_discovery(merchant_schema, evidence_root):
    member = operator()
    source = merchant_source(member, merchant_schema)
    response = sync(member, source["id"])
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SOURCE_NOT_DISCOVERED"


@pytest.mark.db
def test_paypal_sync_without_credentials_fails_closed(evidence_root):
    member = operator()
    source = create_ok(member)
    response = sync(member, source["id"])
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SECRET_UNRESOLVED"
    assert stored_records(member, source["id"]) == []


@pytest.mark.db
def test_sync_is_audited_without_the_secret(merchant_schema, evidence_root):
    member = operator()
    source = merchant_source(member, merchant_schema)
    discover(member, source["id"])
    sync(member, source["id"])
    with scoped_tx(member.tenant) as conn:
        detail = conn.execute(
            text("SELECT detail::text AS detail FROM audit_logs WHERE action = 'source.synced'")
        ).scalar_one()
    assert source["id"] in detail
    assert "SOURCE_SECRET" not in detail
    assert os.environ["MIGRATION_DATABASE_URL"] not in detail


@pytest.mark.db
def test_sync_does_not_write_to_the_merchant(merchant_schema, evidence_root):
    member = operator()
    source = merchant_source(member, merchant_schema)
    discover(member, source["id"])
    sync(member, source["id"])
    engine = create_engine(os.environ["MIGRATION_DATABASE_URL"], poolclass=NullPool)
    with engine.connect() as conn:
        count = conn.execute(
            text(f"SELECT count(*) FROM {merchant_schema}.payments")
        ).scalar_one()
    engine.dispose()
    assert count == 4


@pytest.mark.db
def test_other_tenant_cannot_sync(merchant_schema, evidence_root):
    member = operator()
    source = merchant_source(member, merchant_schema)
    discover(member, source["id"])
    assert sync(operator("B"), source["id"]).status_code == 404
