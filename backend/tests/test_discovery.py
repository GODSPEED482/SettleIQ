"""PostgreSQL and CSV source discovery (tasks 2.5 and 2.6, AC-ONT-01, AC-ONT-02)."""

import os
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text

from app.connectors.manifest import load_capability_registry, load_connector_manifests
from app.db import scoped_tx
from app.semantics import generation
from app.sources import conventions, csv_discovery, evidence
from app.sources.evidence import RetrievalContext
from tests.test_semantics_store import Member

CSV = (
    "\ufeffid,status,currency,amount,created_at,email,note\n"
    "1,PAID,USD,10.50,2026-01-01,a@example.com,\n"
    "2,PAID,EUR,20.00,2026-01-02,b@example.com,x\n"
    "3,REFUNDED,USD,30.25,2026-01-03,c@example.com,\n"
).encode()


def source_body(**overrides) -> dict:
    return {
        "connector_id": "merchant-postgres",
        "display_name": "Merchant database",
        "config": {},
    } | overrides


def create(member: Member, **overrides):
    return member.request("POST", "/api/sources", json=source_body(**overrides))


def create_ok(member: Member, **overrides) -> dict:
    response = create(member, **overrides)
    assert response.status_code == 201, response.text
    return response.json()


def discover(member: Member, source_id: str):
    return member.request("POST", f"/api/sources/{source_id}/discover")


def owner_dsn() -> str:
    raw = os.environ.get("MIGRATION_DATABASE_URL")
    if not raw:
        pytest.skip("MIGRATION_DATABASE_URL is required for discovery fixtures")
    return raw.replace("postgresql+psycopg://", "postgresql://", 1)


@pytest.fixture
def discovery_schema():
    dsn = owner_dsn()
    engine = create_engine(dsn)
    schema = f"discovery_fixture_{uuid4().hex[:8]}"
    with engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        conn.execute(
            text(
                f'CREATE TABLE "{schema}".customers ('
                "id bigint PRIMARY KEY, email text NOT NULL)"
            )
        )
        conn.execute(
            text(
                f'CREATE TABLE "{schema}".orders ('
                "id uuid PRIMARY KEY,"
                "customer_id bigint NOT NULL REFERENCES "
                f'"{schema}".customers(id),'
                "external_ref text NOT NULL,"
                "status text NOT NULL,"
                "currency char(3) NOT NULL,"
                "amount numeric(20,6) NOT NULL,"
                "created_at timestamptz NOT NULL,"
                "note text,"
                "raw jsonb)"
            )
        )
        conn.execute(
            text(
                f"CREATE UNIQUE INDEX orders_external_ref_uq "
                f'ON "{schema}".orders (external_ref)'
            )
        )
        conn.execute(
            text(f'CREATE INDEX orders_created_idx ON "{schema}".orders (created_at)')
        )
        conn.execute(
            text(
                f'INSERT INTO "{schema}".customers VALUES '
                "(1, 'a@example.com'), (2, 'b@example.com')"
            )
        )
        conn.execute(
            text(
                f'INSERT INTO "{schema}".orders VALUES '
                "('00000000-0000-0000-0000-000000000001', 1, 'R1', 'PAID', 'USD', "
                "10.50, '2026-01-01T10:00:00Z', NULL, '{\"a\": 1}'),"
                "('00000000-0000-0000-0000-000000000002', 2, 'R2', 'PAID', 'EUR', "
                "20.00, '2026-01-02T10:00:00Z', 'x', '{\"b\": 2}'),"
                "('00000000-0000-0000-0000-000000000003', 1, 'R3', 'REFUNDED', 'USD', "
                "30.25, '2026-01-03T10:00:00Z', NULL, NULL)"
            )
        )
    try:
        yield schema
    finally:
        with engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


@pytest.fixture
def discovered_postgres(discovery_schema, monkeypatch):
    monkeypatch.setenv("SETTLEIQ_SOURCE_DSN", owner_dsn())
    member = Member()
    source = create_ok(
        member,
        config={"schema": discovery_schema},
        secret_reference="env:SETTLEIQ_SOURCE_DSN",
    )
    response = discover(member, source["id"])
    assert response.status_code == 200, response.text
    return member, source, response.json()


# Contract


def test_merchant_postgres_is_the_discovery_connector():
    manifest = load_connector_manifests()["merchant-postgres"]
    assert manifest.supports_discovery is True
    assert manifest.supports_file_ingestion is False
    assert [capability.key for capability in manifest.capabilities] == [
        "database.schema.read"
    ]
    assert load_capability_registry()["database.schema.read"].kind == "read"


# CSV, no database


def test_csv_discovery_profiles_types_and_aggregates():
    result = csv_discovery.discover(CSV, "orders.csv", "a" * 64, 200_000)
    fields = {field.name: field for field in result.snapshot.fields}
    assert result.object_name == "orders.csv@aaaaaaaaaaaaaaaa"
    assert result.snapshot.object_type == "CSV"
    assert (fields["id"].datatype, fields["id"].unique) == ("integer", True)
    assert fields["status"].datatype == "text"
    assert fields["currency"].datatype == "currency"
    assert fields["amount"].datatype == "decimal"
    assert fields["created_at"].datatype == "date"
    assert fields["note"].nullable is True
    profile = result.profile
    assert (profile.row_count, profile.sampled) == (3, False)
    assert profile.fields["note"]["null_count"] == 2
    assert profile.fields["amount"]["monetary"] is True
    assert profile.fields["amount"]["currency_column"] == "currency"
    assert profile.fields["status"]["distribution"] == [
        {"value": "PAID", "count": 2},
        {"value": "REFUNDED", "count": 1},
    ]
    assert profile.fields["currency"]["currency"] is True
    assert profile.fields["created_at"]["min"] == "2026-01-01"
    assert "distribution" not in profile.fields["email"]


def test_csv_discovery_caps_rows_and_marks_sampled():
    result = csv_discovery.discover(b"id;value\n1;a\n2;b\n", "small.csv", "b" * 64, 1)
    assert (result.profile.row_count, result.profile.sampled) == (1, True)
    assert [field.unique for field in result.snapshot.fields] == [False, False]


def test_csv_leading_zero_identifiers_stay_text():
    result = csv_discovery.discover(b"account\n000123\n000124\n", "ids.csv", "c" * 64, 100)
    assert result.snapshot.fields[0].datatype == "text"


def test_csv_invalid_dates_stay_text():
    result = csv_discovery.discover(
        b"event_date\n2026-13-01\n2026-02-30\n", "dates.csv", "e" * 64, 100
    )
    assert result.snapshot.fields[0].datatype == "text"


@pytest.mark.parametrize(
    "data",
    [b"", b"a,b\n1,2,3\n", b"a,a\n1,2\n", b",b\n1,2\n"],
)
def test_csv_rejects_malformed_files(data):
    with pytest.raises(Exception) as raised:
        csv_discovery.discover(data, "bad.csv", "d" * 64, 100)
    assert getattr(raised.value, "code", None) == "INVALID_CSV"


# PostgreSQL, database


@pytest.mark.db
def test_postgres_discovery_persists_structure_and_aggregates(discovered_postgres):
    member, source, body = discovered_postgres
    schema = source["config"]["schema"]
    orders_name = f"{schema}.orders"
    assert body["kind"] == "postgresql"
    assert body["objects"] == [
        {"object": f"{schema}.customers", "object_type": "TABLE", "fields": 2},
        {"object": orders_name, "object_type": "TABLE", "fields": 9},
    ]
    with scoped_tx(member.tenant) as conn:
        row = conn.execute(
            text(
                "SELECT object_type, schema_snapshot, profile, checksum "
                "FROM source_objects WHERE object_name = :name"
            ),
            {"name": orders_name},
        ).one()
        audit = conn.execute(
            text("SELECT detail FROM audit_logs WHERE action = 'source.discovered'")
        ).one()
        assert conn.execute(text("SELECT count(*) FROM source_records")).scalar() == 0
        assert conn.execute(text("SELECT count(*) FROM raw_evidence")).scalar() == 0
    fields = {field["name"]: field for field in row.schema_snapshot["fields"]}
    assert row.object_type == "TABLE"
    assert row.checksum is None
    assert fields["id"]["unique"] is True
    assert fields["external_ref"]["unique"] is True
    assert row.schema_snapshot["primary_key"] == ["id"]
    assert row.schema_snapshot["foreign_keys"] == [
        {"field": "customer_id", "references": f"{schema}.customers.id"}
    ]
    indexes = {index["name"]: index for index in row.schema_snapshot["indexes"]}
    assert indexes["orders_external_ref_uq"]["unique"] is True
    assert indexes["orders_pkey"]["primary"] is True
    profile = row.profile
    assert (profile["row_count"], profile["sampled"]) == (3, False)
    assert profile["fields"]["note"]["null_count"] == 2
    assert profile["fields"]["status"]["distribution"] == [
        {"value": "PAID", "count": 2},
        {"value": "REFUNDED", "count": 1},
    ]
    assert profile["fields"]["currency"]["currency"] is True
    assert profile["fields"]["amount"]["monetary"] is True
    assert audit.detail == {"source_id": source["id"], "kind": "postgresql", "objects": 2}


@pytest.mark.db
def test_discovered_metadata_feeds_generation(discovered_postgres):
    member, source, _ = discovered_postgres
    schema = source["config"]["schema"]
    with scoped_tx(member.tenant) as conn:
        metadata = generation.load_discovered_metadata(conn)
    orders = next(item for item in metadata if item.object == f"{schema}.orders")
    assert orders.source_id == source["id"]
    assert {key.field for key in orders.foreign_keys} == {"customer_id"}
    amount = next(field for field in orders.fields if field.name == "amount")
    assert amount.profile["monetary"] is True


@pytest.mark.db
def test_unreachable_database_is_502_without_the_secret(monkeypatch):
    monkeypatch.setenv("SETTLEIQ_BAD_DSN", "postgresql://postgres@127.0.0.1:1/settleiq")
    member = Member()
    source = create_ok(member, secret_reference="env:SETTLEIQ_BAD_DSN")
    response = discover(member, source["id"])
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "SOURCE_UNAVAILABLE"
    assert "127.0.0.1" not in response.text


@pytest.mark.db
def test_missing_or_unsupported_secret_is_422():
    member = Member()
    source = create_ok(member)
    response = discover(member, source["id"])
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.db
def test_connector_without_discovery_is_refused():
    member = Member()
    source = create_ok(member, connector_id="internal-merchant")
    response = discover(member, source["id"])
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.db
def test_disabled_source_is_refused():
    member = Member()
    source = create_ok(member)
    with scoped_tx(member.tenant) as conn:
        conn.execute(text("UPDATE data_sources SET status = 'DISABLED'"))
    response = discover(member, source["id"])
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SOURCE_DISABLED"


@pytest.mark.db
def test_cross_tenant_source_is_404():
    owner = Member("A")
    other = Member("B")
    source = create_ok(owner)
    assert discover(other, source["id"]).status_code == 404


# CSV, database


@pytest.mark.db
def test_csv_discovery_reads_stored_evidence(monkeypatch, tmp_path):
    monkeypatch.setattr(evidence, "_root", lambda root=None: tmp_path)
    member = Member()
    source = create_ok(member, connector_id="paypal", protocol="csv")
    with scoped_tx(member.tenant) as conn:
        ref = evidence.store_bytes(
            conn,
            tenant_id=member.tenant,
            source_id=UUID(source["id"]),
            data=CSV,
            evidence_type="FILE",
            content_type="text/csv",
            retrieval_context=RetrievalContext(provenance="tenant_file", file_name="orders.csv"),
            root=tmp_path,
        )
    response = discover(member, source["id"])
    assert response.status_code == 200, response.text
    body = response.json()
    name = conventions.file_object_name("orders.csv", ref.content_hash)
    assert body["kind"] == "csv"
    assert body["objects"] == [{"object": name, "object_type": "CSV", "fields": 7}]
    with scoped_tx(member.tenant) as conn:
        row = conn.execute(text("SELECT checksum, profile FROM source_objects")).one()
        audit = conn.execute(
            text("SELECT detail FROM audit_logs WHERE action = 'source.discovered'")
        ).one()
    assert row.checksum == ref.content_hash
    assert row.profile["row_count"] == 3
    assert audit.detail == {"source_id": source["id"], "kind": "csv", "objects": 1}


@pytest.mark.db
def test_generation_reads_only_the_latest_csv(monkeypatch, tmp_path):
    monkeypatch.setattr(evidence, "_root", lambda root=None: tmp_path)
    member = Member()
    source = create_ok(member, connector_id="paypal", protocol="csv")
    with scoped_tx(member.tenant) as conn:
        evidence.store_bytes(
            conn,
            tenant_id=member.tenant,
            source_id=UUID(source["id"]),
            data=CSV,
            evidence_type="FILE",
            content_type="text/csv",
            retrieval_context=RetrievalContext(provenance="tenant_file", file_name="orders.csv"),
            root=tmp_path,
        )
    assert discover(member, source["id"]).status_code == 200
    with scoped_tx(member.tenant) as conn:
        conn.execute(text("UPDATE source_objects SET discovered_at = now() - interval '1 hour'"))
    with scoped_tx(member.tenant) as conn:
        latest_ref = evidence.store_bytes(
            conn,
            tenant_id=member.tenant,
            source_id=UUID(source["id"]),
            data=b"id,status\n1,SHIPPED\n",
            evidence_type="FILE",
            content_type="text/csv",
            retrieval_context=RetrievalContext(provenance="tenant_file", file_name="orders.csv"),
            root=tmp_path,
        )
    assert discover(member, source["id"]).status_code == 200
    latest = conventions.file_object_name("orders.csv", latest_ref.content_hash)
    with scoped_tx(member.tenant) as conn:
        metadata = generation.load_discovered_metadata(conn)
    assert [item.object for item in metadata] == [latest]
    assert len(metadata[0].fields) == 2


@pytest.mark.db
def test_csv_discovery_without_evidence_is_409():
    member = Member()
    source = create_ok(member, connector_id="paypal", protocol="csv")
    response = discover(member, source["id"])
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "NO_EVIDENCE"


@pytest.mark.db
def test_csv_discovery_rejects_a_bad_stored_file(monkeypatch, tmp_path):
    monkeypatch.setattr(evidence, "_root", lambda root=None: tmp_path)
    member = Member()
    source = create_ok(member, connector_id="paypal", protocol="csv")
    with scoped_tx(member.tenant) as conn:
        evidence.store_bytes(
            conn,
            tenant_id=member.tenant,
            source_id=UUID(source["id"]),
            data=b"a,b\n1,2,3\n",
            evidence_type="FILE",
            content_type="text/csv",
            retrieval_context=RetrievalContext(provenance="tenant_file", file_name="bad.csv"),
            root=tmp_path,
        )
    response = discover(member, source["id"])
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_CSV"
