"""Source registry, raw evidence and canonical records (tasks 1.6/1.7, AC-OPEN-01, AC-REC-02)."""

import hashlib
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.connectors.manifest import load_connector_manifests
from app.db import scoped_tx
from app.errors import ApiError
from app.sources import evidence, records, registry
from app.sources.envelope import SourceRecordEnvelope
from tests.test_semantics_store import Member

PAYPAL = "paypal"


def body(**overrides) -> dict:
    return {
        "connector_id": PAYPAL,
        "display_name": "PayPal Sandbox",
        "config": {"webhook_id": "WH-1"},
    } | overrides


def create(member: Member, **overrides):
    return member.request("POST", "/api/sources", json=body(**overrides))


def create_ok(member: Member, **overrides) -> dict:
    response = create(member, **overrides)
    assert response.status_code == 201, response.text
    return response.json()


def add_object(member: Member, source_id: str) -> UUID:
    object_id = uuid4()
    with scoped_tx(member.tenant) as conn:
        conn.execute(
            text(
                """
                INSERT INTO source_objects (id, tenant_id, source_id, object_name, object_type)
                VALUES (:id, :tenant, :source, 'captures', 'API')
                """
            ),
            {"id": object_id, "tenant": member.tenant, "source": UUID(source_id)},
        )
    return object_id


def store(member: Member, source_id: str, data: bytes = b'{"id":"CAP-1"}', root=None):
    with scoped_tx(member.tenant) as conn:
        return evidence.store_bytes(
            conn,
            tenant_id=member.tenant,
            source_id=UUID(source_id),
            data=data,
            evidence_type="RECORD",
            content_type="application/json",
            root=root,
        )


def envelope(member: Member, source_id: str, object_id: UUID, raw) -> SourceRecordEnvelope:
    return SourceRecordEnvelope(
        id=uuid4(),
        tenant_id=member.tenant,
        source_id=UUID(source_id),
        source_object_id=object_id,
        external_id="CAP-1",
        record_type="finops:Capture",
        attributes={"id": "CAP-1", "amount": {"value": "10.00", "currency_code": "USD"}},
        raw_evidence_id=raw.id,
        occurred_at="2026-01-01T00:00:00Z",
        observed_at="2026-01-01T00:00:01Z",
        payload_hash=raw.content_hash,
        connector_version="1.0.0",
        mapping_version=None,
    )


# Registry


@pytest.mark.db
def test_created_source_carries_the_manifest_capabilities():
    member = Member()
    source = create_ok(member)
    declared = [c.key for c in load_connector_manifests()[PAYPAL].capabilities]
    assert source["connector_id"] == PAYPAL
    assert source["connector_version"] == "1.0.0"
    assert source["source_type"] == "payment_provider"
    assert [c["key"] for c in source["capabilities"]] == declared
    assert source["has_secret_reference"] is False


@pytest.mark.db
def test_source_is_listed_for_its_tenant():
    member = Member()
    created = create_ok(member)
    listed = member.request("GET", "/api/sources").json()
    assert [item["id"] for item in listed] == [created["id"]]


@pytest.mark.db
def test_secret_reference_is_stored_but_never_returned():
    member = Member()
    created = create_ok(member, secret_reference="ssm://settleiq/paypal-sandbox")
    assert created["has_secret_reference"] is True
    assert "secret_reference" not in created
    assert "ssm://" not in str(created)
    with scoped_tx(member.tenant) as conn:
        stored = conn.execute(text("SELECT secret_reference FROM data_sources")).scalar_one()
    assert stored == "ssm://settleiq/paypal-sandbox"


@pytest.mark.db
def test_unknown_connector_is_refused():
    member = Member()
    response = create(member, connector_id="nope")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.db
def test_source_binds_a_declared_protocol():
    member = Member()
    source = create_ok(member, protocol="csv")
    assert source["protocol"] == "csv"
    assert source["source_type"] == "payment_provider"


@pytest.mark.db
@pytest.mark.parametrize("override", [{"protocol": "ftp"}, {"source_type": "crm"}])
def test_undeclared_protocol_or_source_type_is_refused(override):
    member = Member()
    response = create(member, **override)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert member.request("GET", "/api/sources").json() == []


@pytest.mark.db
@pytest.mark.parametrize("field", ["status", "tenant_id", "capabilities", "created_by"])
def test_request_cannot_set_server_owned_fields(field):
    member = Member()
    assert create(member, **{field: "x"}).status_code == 422


@pytest.mark.db
@pytest.mark.parametrize("key", ["client_secret", "password", "access_token", "api_key"])
def test_config_refuses_secret_values(key):
    member = Member()
    response = create(member, config={"webhook_id": "WH-1", key: "raw-secret"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.db
def test_a_non_payment_connector_uses_the_same_source_model():
    member = Member()
    source = create_ok(member, connector_id="internal-merchant", display_name="Merchant DB")
    assert source["source_type"] == "merchant_database"
    assert source["protocol"] == "postgresql"
    assert {c["key"] for c in source["capabilities"]} >= {"merchant.payment.read"}


@pytest.mark.db
def test_sources_do_not_leak_across_tenants():
    owner = Member("A")
    other = Member("B")
    create_ok(owner)
    assert other.request("GET", "/api/sources").json() == []
    with scoped_tx(other.tenant) as conn:
        assert conn.execute(text("SELECT count(*) FROM data_sources")).scalar() == 0


# Raw evidence


@pytest.mark.db
def test_evidence_is_content_addressed_and_idempotent(tmp_path):
    member = Member()
    source = create_ok(member)
    first = store(member, source["id"], root=tmp_path)
    second = store(member, source["id"], root=tmp_path)
    assert first.id == second.id
    assert first.content_hash == hashlib.sha256(b'{"id":"CAP-1"}').hexdigest()
    assert (tmp_path / first.storage_key).read_bytes() == b'{"id":"CAP-1"}'
    with scoped_tx(member.tenant) as conn:
        assert conn.execute(text("SELECT count(*) FROM raw_evidence")).scalar() == 1


@pytest.mark.db
def test_raw_evidence_is_append_only_for_the_app_role(tmp_path):
    member = Member()
    source = create_ok(member)
    store(member, source["id"], root=tmp_path)
    with pytest.raises(DBAPIError, match="permission denied"):
        with scoped_tx(member.tenant) as conn:
            conn.execute(text("UPDATE raw_evidence SET byte_size = 0"))


# Canonical records


@pytest.mark.db
def test_record_round_trip(tmp_path):
    member = Member()
    source = create_ok(member)
    object_id = add_object(member, source["id"])
    raw = store(member, source["id"], root=tmp_path)
    record = envelope(member, source["id"], object_id, raw)
    with scoped_tx(member.tenant) as conn:
        assert records.persist_record(conn, record) == record.id
    with scoped_tx(member.tenant) as conn:
        row = conn.execute(
            text("SELECT record_type, attributes, payload_hash FROM source_records")
        ).one()
    assert row.record_type == "finops:Capture"
    assert row.attributes["amount"]["value"] == "10.00"
    assert row.payload_hash == raw.content_hash


@pytest.mark.db
def test_repeated_external_id_does_not_duplicate_the_record(tmp_path):
    member = Member()
    source = create_ok(member)
    object_id = add_object(member, source["id"])
    raw = store(member, source["id"], root=tmp_path)
    first = envelope(member, source["id"], object_id, raw)
    second = first.model_copy(update={"id": uuid4()})
    with scoped_tx(member.tenant) as conn:
        assert records.persist_record(conn, first) == first.id
        assert records.persist_record(conn, second) == first.id
        assert conn.execute(text("SELECT count(*) FROM source_records")).scalar() == 1


@pytest.mark.db
def test_record_hash_must_match_its_evidence(tmp_path):
    member = Member()
    source = create_ok(member)
    object_id = add_object(member, source["id"])
    raw = store(member, source["id"], root=tmp_path)
    forged = envelope(member, source["id"], object_id, raw).model_copy(
        update={"payload_hash": "0" * 64}
    )
    with pytest.raises(ApiError, match="payload_hash"):
        with scoped_tx(member.tenant) as conn:
            records.persist_record(conn, forged)
    with scoped_tx(member.tenant) as conn:
        assert conn.execute(text("SELECT count(*) FROM source_records")).scalar() == 0


@pytest.mark.db
def test_record_cannot_borrow_another_tenants_evidence(tmp_path):
    owner = Member("A")
    other = Member("B")
    source_a = create_ok(owner)
    source_b = create_ok(other)
    object_b = add_object(other, source_b["id"])
    raw_a = store(owner, source_a["id"], root=tmp_path)
    cross = SourceRecordEnvelope(
        id=uuid4(),
        tenant_id=other.tenant,
        source_id=UUID(source_b["id"]),
        source_object_id=object_b,
        external_id=None,
        record_type="finops:Capture",
        attributes={},
        raw_evidence_id=raw_a.id,
        occurred_at=None,
        observed_at="2026-01-01T00:00:01Z",
        payload_hash=raw_a.content_hash,
        connector_version="1.0.0",
        mapping_version=None,
    )
    with pytest.raises(ApiError, match="Raw evidence"):
        with scoped_tx(other.tenant) as conn:
            records.persist_record(conn, cross)


# Trusted webhook lookup


@pytest.mark.db
def test_webhook_lookup_derives_the_tenant_without_a_tenant_context():
    member = Member()
    source = create_ok(member)
    found = registry.webhook_source(UUID(source["id"]))
    assert found is not None
    assert found["tenant_id"] == member.tenant
    assert found["connector_id"] == PAYPAL
    assert found["config"] == {"webhook_id": "WH-1"}
    assert registry.webhook_source(uuid4()) is None
