"""Source discovery and profiling (tasks 2.5 and 2.6, AC-ONT-01, AC-ONT-02)."""

import json
import os
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from app.db import scoped_tx
from tests.test_semantics_store import Member
from tests.test_sources import create_ok

SECRET = "env:SOURCE_SECRET_TEST_MERCHANT"

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
CREATE INDEX payments_status_idx ON {schema}.payments (status);
CREATE VIEW {schema}.completed_payments AS
    SELECT payment_id, amount FROM {schema}.payments WHERE status = 'COMPLETED';
INSERT INTO {schema}.customers VALUES (1, 'alice@example.com'), (2, 'bob@example.com');
INSERT INTO {schema}.payments VALUES
    ('PAY-1', 1, 'ORD-1', 10.00, 'USD', 'COMPLETED', '2026-10-03T10:00:00Z', 'secret note'),
    ('PAY-2', 1, 'ORD-2', 25.50, 'USD', 'PENDING', '2026-11-01T10:00:00Z', NULL),
    ('PAY-3', 2, 'ORD-2', 99.99, 'EUR', 'COMPLETED', '2026-11-02T10:00:00Z', NULL),
    ('PAY-4', NULL, 'ORD-4', 4.25, 'USD', 'COMPLETED', '2026-11-05T10:00:00Z', NULL);
"""


@pytest.fixture
def merchant_schema(monkeypatch):
    """A merchant schema in the test database, reached as a source would reach it."""
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


def merchant_source(member: Member, schema: str, **overrides) -> dict:
    return create_ok(
        member,
        connector_id="merchant-postgres",
        display_name="Merchant DB",
        config={"schema": schema},
        secret_reference=SECRET,
        **overrides,
    )


def discover(member: Member, source_id: str):
    return member.request("POST", f"/api/sources/{source_id}/discover")


def stored_objects(member: Member, source_id: str) -> dict[str, dict]:
    with scoped_tx(member.tenant) as conn:
        rows = conn.execute(
            text(
                """
                SELECT object_name, object_type, schema_snapshot, profile, discovered_at
                FROM source_objects WHERE source_id = :source
                """
            ),
            {"source": UUID(source_id)},
        ).all()
    return {row.object_name: row._asdict() for row in rows}


@pytest.fixture
def discovered(merchant_schema):
    member = Member()
    source = merchant_source(member, merchant_schema)
    response = discover(member, source["id"])
    assert response.status_code == 200, response.text
    return member, source, response.json()


# PostgreSQL (2.5)


@pytest.mark.db
def test_discovery_lists_tables_and_views(discovered):
    member, source, result = discovered
    assert result["source_id"] == source["id"]
    found = {item["object_name"]: item["object_type"] for item in result["objects"]}
    assert found == {"customers": "TABLE", "payments": "TABLE", "completed_payments": "VIEW"}
    assert set(stored_objects(member, source["id"])) == set(found)


@pytest.mark.db
def test_schema_snapshot_exposes_the_structure(discovered):
    member, source, _ = discovered
    snapshot = stored_objects(member, source["id"])["payments"]["schema_snapshot"]
    columns = {column["name"]: column for column in snapshot["columns"]}
    assert columns["amount"]["type"] == "NUMERIC(20, 6)"
    assert columns["amount"]["datatype"] == "decimal"
    assert columns["created_at"]["datatype"] == "datetime"
    assert columns["amount"]["nullable"] is False
    assert columns["note"]["nullable"] is True
    assert snapshot["primary_key"] == ["payment_id"]
    assert columns["payment_id"]["primary_key"] and columns["payment_id"]["unique"]
    assert snapshot["foreign_keys"][0]["columns"] == ["customer_id"]
    assert snapshot["foreign_keys"][0]["references"]["table"] == "customers"
    assert snapshot["foreign_keys"][0]["references"]["columns"] == ["id"]
    assert {"name": "payments_status_idx", "columns": ["status"], "unique": False} in snapshot[
        "indexes"
    ]
    customers = stored_objects(member, source["id"])["customers"]["schema_snapshot"]
    assert customers["unique_constraints"][0]["columns"] == ["email"]
    assert {c["name"]: c["unique"] for c in customers["columns"]} == {"id": True, "email": True}


@pytest.mark.db
def test_profile_exposes_aggregates_only(discovered):
    member, source, _ = discovered
    objects = stored_objects(member, source["id"])
    profile = objects["payments"]["profile"]
    columns = profile["columns"]
    assert profile["row_count"] == 4
    assert columns["note"]["null_count"] == 3
    assert columns["customer_id"]["null_count"] == 1
    assert columns["order_ref"]["distinct_count"] == 3
    assert columns["amount"]["min"] == "4.250000"
    assert columns["amount"]["max"] == "99.990000"
    assert columns["currency"]["currency_distribution"] == {"USD": 3, "EUR": 1}
    assert columns["status"]["status_distribution"] == {"COMPLETED": 3, "PENDING": 1}
    assert columns["created_at"]["temporal_distribution"] == {"2026-10": 1, "2026-11": 3}
    assert columns["payment_id"]["duplicate_identifiers"] == 0
    stored = json.dumps([item["profile"] for item in objects.values()])
    for raw in ("PAY-1", "ORD-2", "secret note", "alice@example.com"):
        assert raw not in stored


@pytest.mark.db
def test_duplicate_identifiers_are_counted(merchant_schema):
    member = Member()
    source = merchant_source(member, merchant_schema)
    discover(member, source["id"])
    customer_id = stored_objects(member, source["id"])["payments"]["profile"]["columns"][
        "customer_id"
    ]
    assert customer_id["duplicate_identifiers"] == 1


@pytest.mark.db
def test_discovery_is_audited_without_the_secret(discovered, merchant_schema):
    member, source, _ = discovered
    with scoped_tx(member.tenant) as conn:
        rows = conn.execute(
            text("SELECT detail::text AS detail FROM audit_logs WHERE action = 'source.discovered'")
        ).all()
    assert len(rows) == 1
    assert source["id"] in rows[0].detail
    assert "SOURCE_SECRET" not in rows[0].detail
    assert os.environ["MIGRATION_DATABASE_URL"] not in rows[0].detail


@pytest.mark.db
def test_rediscovery_updates_in_place(discovered):
    member, source, _ = discovered
    before = stored_objects(member, source["id"])
    assert discover(member, source["id"]).status_code == 200
    after = stored_objects(member, source["id"])
    assert set(after) == set(before)
    assert after["payments"]["discovered_at"] > before["payments"]["discovered_at"]


@pytest.mark.db
def test_connector_without_discovery_is_refused():
    member = Member()
    source = create_ok(member)
    response = discover(member, source["id"])
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DISCOVERY_UNSUPPORTED"


@pytest.mark.db
def test_unresolved_secret_is_refused(monkeypatch):
    monkeypatch.delenv("SOURCE_SECRET_TEST_MERCHANT", raising=False)
    member = Member()
    source = merchant_source(member, "public")
    response = discover(member, source["id"])
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SECRET_UNRESOLVED"


@pytest.mark.db
def test_unreachable_source_names_no_connection_detail(monkeypatch):
    monkeypatch.setenv(
        "SOURCE_SECRET_TEST_MERCHANT", "postgresql://reader:hunter2@127.0.0.1:1/merchant"
    )
    member = Member()
    source = merchant_source(member, "public")
    response = discover(member, source["id"])
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SOURCE_UNREACHABLE"
    assert "hunter2" not in response.text and "reader" not in response.text


@pytest.mark.db
def test_discovery_does_not_write_to_the_source(discovered, merchant_schema):
    engine = create_engine(os.environ["MIGRATION_DATABASE_URL"], poolclass=NullPool)
    with engine.connect() as conn:
        count = conn.execute(text(f"SELECT count(*) FROM {merchant_schema}.payments")).scalar()
    engine.dispose()
    assert count == 4


@pytest.mark.db
@pytest.mark.parametrize("source_id", ["not-a-uuid", str(uuid4())])
def test_unknown_source_is_404(source_id):
    response = discover(Member(), source_id)
    assert response.status_code == 404


@pytest.mark.db
def test_other_tenant_cannot_discover_or_read_objects(discovered):
    member, source, _ = discovered
    intruder = Member("B")
    assert discover(intruder, source["id"]).status_code == 404
    with scoped_tx(intruder.tenant) as conn:
        assert conn.execute(text("SELECT count(*) FROM source_objects")).scalar_one() == 0

