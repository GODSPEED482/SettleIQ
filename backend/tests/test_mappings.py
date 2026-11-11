"""Versioned mappings and authority assertions (task 2.4, ARCH-07, AC-REC-10, AC-REC-15)."""

import sys
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import scoped_tx
from app.semantics import mappings
from app.semantics.mappings import AuthorityBody, MappingBody, Transform
from tests.test_semantics_store import Member

PAYPAL = "11111111-1111-4111-8111-111111111111"
MERCHANT = "22222222-2222-4222-8222-222222222222"


def mapping(draft_id: str | UUID, **overrides) -> dict:
    return {
        "draft_id": str(draft_id),
        "source_id": PAYPAL,
        "external_object": "captures",
        "field_path": "$.amount.value",
        "property_iri": "finops:amount",
        "class_iri": "finops:Capture",
        "datatype": "decimal",
        "transform": {"name": "decimal_string"},
        "nullable": False,
        "is_unique": False,
        "sync_strategy": "event",
        "value_basis": "captured",
        "unit": None,
        "currency_field": "$.amount.currency_code",
        "temporal_context": "at_capture",
        "method": "human",
    } | overrides


def authority(draft_id: str | UUID, **overrides) -> dict:
    return {
        "draft_id": str(draft_id),
        "property_iri": "finops:lifecycleStatus",
        "entity_type_iri": "finops:Capture",
        "source_id": PAYPAL,
        "valid_from": "2026-01-01T00:00:00Z",
        "valid_to": None,
        "verification_method": "signed webhook and capture read",
    } | overrides


# No database


@pytest.mark.parametrize("name", ["eval", "sql", "python", "jsonpath_exec"])
def test_transform_outside_the_allowlist_is_refused(name):
    with pytest.raises(ValidationError):
        Transform(name=name)


def test_enum_map_needs_values_and_others_refuse_them():
    assert Transform(name="enum_map", values={"A": "B"}).values == {"A": "B"}
    with pytest.raises(ValidationError):
        Transform(name="enum_map")
    with pytest.raises(ValidationError):
        Transform(name="upper", values={"A": "B"})


@pytest.mark.parametrize("field", ["sql", "expression", "tenant_id", "status", "version"])
def test_mapping_body_refuses_unknown_fields(field):
    with pytest.raises(ValidationError):
        MappingBody(**mapping(uuid4(), **{field: "x"}))


@pytest.mark.parametrize("iri", ["amount", "finops:", "finops:a b", "http://x/y", "'; DROP--:a"])
def test_mapping_property_must_be_a_prefixed_term(iri):
    with pytest.raises(ValidationError):
        MappingBody(**mapping(uuid4(), property_iri=iri))


def test_authority_interval_must_move_forward():
    with pytest.raises(ValidationError, match="valid_to"):
        AuthorityBody(**authority(uuid4(), valid_to="2025-01-01T00:00:00Z"))


def test_authority_needs_a_timezone():
    with pytest.raises(ValidationError):
        AuthorityBody(**authority(uuid4(), valid_from="2026-01-01T00:00:00"))


def row(**overrides) -> dict:
    body = mapping(uuid4(), **overrides)
    return {key: value for key, value in body.items() if key != "draft_id"}


def test_two_comparable_bindings_are_allowed():
    assert mappings.comparability_violations([row(), row(source_id=MERCHANT)]) == []


@pytest.mark.parametrize(
    "change",
    [
        {"value_basis": "fulfilled"},
        {"unit": "cents"},
        {"datatype": "string"},
        {"temporal_context": "at_settlement"},
        {"currency_field": None},
    ],
)
def test_incomparable_bindings_to_one_property_are_refused(change):
    violations = mappings.comparability_violations([row(), row(source_id=MERCHANT, **change)])
    assert violations and "finops:amount" in violations[0]


def test_different_properties_are_never_compared():
    other = row(property_iri="finops:lifecycleStatus", datatype="string", currency_field=None)
    assert mappings.comparability_violations([row(), other]) == []


def test_unknown_terms_are_named():
    found = mappings.unknown_term_violations([row()], [], {"finops:Capture"})
    assert found == ["Unknown ontology term: finops:amount"]


# Database


def register_sources(member: Member) -> None:
    with scoped_tx(member.tenant) as conn:
        for source_id, connector in ((PAYPAL, "paypal"), (MERCHANT, "internal-merchant")):
            conn.execute(
                text(
                    """
                    INSERT INTO data_sources (id, tenant_id, connector_id, connector_version,
                        source_type, protocol, display_name, created_by)
                    VALUES (:id, :tenant, :connector, '1.0.0', 'payment_provider', 'http',
                        :name, :user)
                    """
                ),
                {
                    "id": UUID(source_id),
                    "tenant": member.tenant,
                    "connector": connector,
                    "name": connector,
                    "user": member.principal.id,
                },
            )


@pytest.fixture
def draft_member(monkeypatch) -> tuple[Member, str]:
    # Source IDs are globally unique, so each test tenant needs its own pair. The factories
    # below read these module names, so patching them keeps every call site unchanged.
    monkeypatch.setattr(sys.modules[__name__], "PAYPAL", str(uuid4()))
    monkeypatch.setattr(sys.modules[__name__], "MERCHANT", str(uuid4()))
    member = Member()
    member.activate("finops")
    register_sources(member)
    return member, member.create_draft().json()["id"]


def save(member: Member, body: dict):
    return member.request("POST", "/api/mappings", json=body)


def assert_authority(member: Member, body: dict):
    return member.request("POST", "/api/authority", json=body)


def listed(member: Member, draft_id: str) -> list[dict]:
    return member.request("GET", "/api/mappings", params={"draft_id": draft_id}).json()


def validate(member: Member, draft_id: str) -> dict:
    return member.request("POST", "/api/ontology/validate", json={"draft_id": draft_id}).json()


def results(body: dict) -> dict[str, dict]:
    return {item["validator"]: item for item in body["results"]}


@pytest.mark.db
def test_two_sources_bind_to_one_property_as_separate_entries(draft_member):
    member, draft = draft_member
    assert save(member, mapping(draft)).status_code == 201
    second = save(member, mapping(draft, source_id=MERCHANT, external_object="payments"))
    assert second.status_code == 201
    rows = listed(member, draft)
    assert {r["source_id"] for r in rows} == {PAYPAL, MERCHANT}
    assert {r["property_iri"] for r in rows} == {"finops:amount"}
    body = validate(member, draft)
    assert body["passed"] is True
    assert results(body)["mappings.comparable"]["passed"] is True


@pytest.mark.db
def test_fulfilled_value_cannot_share_a_property_with_a_captured_value(draft_member):
    member, draft = draft_member
    save(member, mapping(draft))
    save(member, mapping(draft, source_id=MERCHANT, value_basis="fulfilled"))
    body = validate(member, draft)
    assert body["passed"] is False
    assert results(body)["mappings.comparable"]["passed"] is False


@pytest.mark.db
def test_fulfilled_value_on_a_distinct_property_is_fine(draft_member):
    member, draft = draft_member
    term = {"@id": "tenant:fulfilledAmount", "@type": "owl:DatatypeProperty",
            "rdfs:label": "fulfilled amount", "rdfs:domain": "finops:FinancialEntity"}
    open_draft = member.request("GET", "/api/ontology/drafts/open").json()
    member.edit(open_draft, [term])
    save(member, mapping(draft))
    save(
        member,
        mapping(draft, source_id=MERCHANT, value_basis="fulfilled",
                property_iri="tenant:fulfilledAmount"),
    )
    assert validate(member, draft)["passed"] is True


@pytest.mark.db
def test_mapping_to_an_unknown_term_blocks_validation(draft_member):
    member, draft = draft_member
    save(member, mapping(draft, property_iri="finops:invented"))
    body = validate(member, draft)
    assert body["passed"] is False
    assert "finops:invented" in results(body)["mappings.terms_exist"]["messages"][0]


@pytest.mark.db
def test_saving_again_adds_a_version_and_keeps_the_old_row(draft_member):
    member, draft = draft_member
    first = save(member, mapping(draft)).json()
    second = save(member, mapping(draft, nullable=True)).json()
    assert (first["version"], second["version"]) == (1, 2)
    rows = listed(member, draft)
    assert [(r["version"], r["nullable"]) for r in rows] == [(2, True)]
    with scoped_tx(member.tenant) as conn:
        assert conn.execute(text("SELECT count(*) FROM source_mappings")).scalar() == 2


@pytest.mark.db
def test_retiring_hides_a_mapping_but_keeps_history(draft_member):
    member, draft = draft_member
    created = save(member, mapping(draft)).json()
    assert member.request("POST", f"/api/mappings/{created['id']}/retire").status_code == 200
    assert listed(member, draft) == []
    with scoped_tx(member.tenant) as conn:
        statuses = conn.execute(
            text("SELECT status FROM source_mappings ORDER BY version")
        ).scalars().all()
    assert statuses == ["ACTIVE", "RETIRED"]


@pytest.mark.db
def test_a_retired_mapping_can_be_restored_with_a_new_version(draft_member):
    member, draft = draft_member
    created = save(member, mapping(draft)).json()
    member.request("POST", f"/api/mappings/{created['id']}/retire")
    assert save(member, mapping(draft)).json()["version"] == 3
    assert len(listed(member, draft)) == 1


@pytest.mark.db
def test_a_mapping_change_invalidates_an_earlier_validation(draft_member):
    member, draft = draft_member
    save(member, mapping(draft))
    first = validate(member, draft)
    save(member, mapping(draft, nullable=True))
    assert validate(member, draft)["content_hash"] != first["content_hash"]


@pytest.mark.db
def test_an_authority_change_invalidates_an_earlier_validation(draft_member):
    member, draft = draft_member
    first = validate(member, draft)
    assert assert_authority(member, authority(draft)).status_code == 201
    assert validate(member, draft)["content_hash"] != first["content_hash"]


@pytest.mark.db
def test_row_versions_do_not_change_the_hash_when_the_content_is_the_same(draft_member):
    member, draft = draft_member
    save(member, mapping(draft))
    first = validate(member, draft)["content_hash"]
    save(member, mapping(draft))
    assert validate(member, draft)["content_hash"] == first


@pytest.mark.db
def test_authority_is_fact_specific(draft_member):
    member, draft = draft_member
    assert_authority(member, authority(draft))
    assert_authority(
        member,
        authority(draft, source_id=MERCHANT, entity_type_iri="finops:Payment"),
    )
    rows = member.request("GET", "/api/authority", params={"draft_id": draft}).json()
    assert {(r["entity_type_iri"], r["source_id"]) for r in rows} == {
        ("finops:Capture", PAYPAL),
        ("finops:Payment", MERCHANT),
    }


@pytest.mark.db
def test_overlapping_authority_is_allowed_here_and_resolved_later(draft_member):
    member, draft = draft_member
    assert_authority(member, authority(draft))
    assert_authority(member, authority(draft, source_id=MERCHANT))
    assert validate(member, draft)["passed"] is True


@pytest.mark.db
def test_authority_on_an_unknown_term_blocks_validation(draft_member):
    member, draft = draft_member
    assert_authority(member, authority(draft, entity_type_iri="finops:Invented"))
    assert validate(member, draft)["passed"] is False


@pytest.mark.db
def test_authority_can_be_retired(draft_member):
    member, draft = draft_member
    created = assert_authority(member, authority(draft)).json()
    assert member.request("POST", f"/api/authority/{created['id']}/retire").status_code == 200
    assert member.request("GET", "/api/authority", params={"draft_id": draft}).json() == []


@pytest.mark.db
def test_changes_are_audited(draft_member):
    member, draft = draft_member
    save(member, mapping(draft))
    assert_authority(member, authority(draft))
    with scoped_tx(member.tenant) as conn:
        actions = set(conn.execute(text("SELECT action FROM audit_logs")).scalars())
    assert {"mapping.saved", "authority.saved"} <= actions


@pytest.mark.db
def test_closed_draft_cannot_change(draft_member):
    member, draft = draft_member
    created = save(member, mapping(draft)).json()
    member.request("POST", f"/api/ontology/drafts/{draft}/abandon")
    assert save(member, mapping(draft)).status_code == 409
    assert assert_authority(member, authority(draft)).status_code == 409
    assert member.request("POST", f"/api/mappings/{created['id']}/retire").status_code == 409


@pytest.mark.db
@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE source_mappings SET version = 9",
        "DELETE FROM source_mappings",
        "UPDATE authority_assertions SET version = 9",
        "DELETE FROM authority_assertions",
    ],
)
def test_mappings_and_authority_are_append_only(draft_member, statement):
    member, _ = draft_member
    with pytest.raises(DBAPIError, match="permission denied"):
        with scoped_tx(member.tenant) as conn:
            conn.execute(text(statement))


@pytest.mark.db
def test_a_binding_or_assertion_needs_a_registered_source(draft_member):
    member, draft = draft_member
    missing = str(uuid4())
    assert save(member, mapping(draft, source_id=missing)).status_code == 422
    assert assert_authority(member, authority(draft, source_id=missing)).status_code == 422
    assert listed(member, draft) == []


@pytest.mark.db
def test_another_tenants_source_looks_like_a_missing_one(draft_member):
    member, draft = draft_member
    other = Member("Other")
    foreign = str(uuid4())
    with scoped_tx(other.tenant) as conn:
        conn.execute(
            text(
                """
                INSERT INTO data_sources (id, tenant_id, connector_id, connector_version,
                    source_type, protocol, display_name, created_by)
                VALUES (:id, :tenant, 'paypal', '1.0.0', 'payment_provider', 'http', 'x', :user)
                """
            ),
            {"id": foreign, "tenant": other.tenant, "user": other.principal.id},
        )
    refused = save(member, mapping(draft, source_id=foreign))
    absent = save(member, mapping(draft, source_id=str(uuid4())))
    assert refused.status_code == 422
    assert refused.json() == absent.json()


def insert_mapping_row(member: Member, draft: str, source: str | UUID) -> None:
    """Straight into the table as the runtime role, past the route's own checks."""
    with scoped_tx(member.tenant) as conn:
        conn.execute(
            text(
                """
                INSERT INTO source_mappings (id, tenant_id, draft_id, source_id,
                    external_object, field_path, property_iri, class_iri, version, status,
                    datatype, transform, nullable, is_unique, sync_strategy, value_basis,
                    temporal_context, method, created_by)
                VALUES (:id, :tenant, :draft, :source, 'o', 'f', 'finops:amount',
                    'finops:Capture', 1, 'ACTIVE', 'decimal', '{}', false, false, 'event',
                    'captured', 'at_capture', 'human', :user)
                """
            ),
            {"id": uuid4(), "tenant": member.tenant, "draft": draft, "source": source,
             "user": member.principal.id},
        )


def insert_authority_row(member: Member, draft: str, source: str | UUID) -> None:
    with scoped_tx(member.tenant) as conn:
        conn.execute(
            text(
                """
                INSERT INTO authority_assertions (id, tenant_id, draft_id, property_iri,
                    entity_type_iri, source_id, version, status, valid_from,
                    verification_method, created_by)
                VALUES (:id, :tenant, :draft, 'finops:amount', 'finops:Capture', :source, 1,
                    'ACTIVE', now(), 'read', :user)
                """
            ),
            {"id": uuid4(), "tenant": member.tenant, "draft": draft, "source": source,
             "user": member.principal.id},
        )


def mark_published(member: Member, draft: str) -> None:
    with scoped_tx(member.tenant) as conn:
        conn.execute(
            text("UPDATE ontology_drafts SET status = 'PUBLISHED' WHERE id = :id"), {"id": draft}
        )


@pytest.mark.db
def test_the_database_also_refuses_an_unknown_source(draft_member):
    member, draft = draft_member
    with pytest.raises(DBAPIError, match="source_mappings_source_fk"):
        insert_mapping_row(member, draft, uuid4())


@pytest.mark.db
def test_an_open_draft_still_takes_bindings_at_the_database(draft_member):
    member, draft = draft_member
    insert_mapping_row(member, draft, PAYPAL)
    insert_authority_row(member, draft, PAYPAL)


@pytest.mark.db
@pytest.mark.parametrize("insert", [insert_mapping_row, insert_authority_row])
def test_a_published_draft_takes_no_new_bindings(draft_member, insert):
    member, draft = draft_member
    mark_published(member, draft)
    with pytest.raises(DBAPIError, match="immutable"):
        insert(member, draft, PAYPAL)


@pytest.mark.db
@pytest.mark.parametrize(
    "assignment",
    ["status = 'OPEN'", "revision = 99", "tenant_extension = '{}'", "proposals = '[]'"],
)
def test_a_published_draft_cannot_be_updated(draft_member, assignment):
    member, draft = draft_member
    mark_published(member, draft)
    with pytest.raises(DBAPIError, match="immutable"):
        with scoped_tx(member.tenant) as conn:
            conn.execute(
                text(f"UPDATE ontology_drafts SET {assignment} WHERE id = :id"), {"id": draft}
            )


@pytest.mark.db
def test_other_tenants_cannot_use_or_see_a_draft(draft_member):
    member, draft = draft_member
    created = save(member, mapping(draft)).json()
    other = Member("Other")
    assert save(other, mapping(draft)).status_code == 404
    assert listed(other, draft) == []
    assert other.request("POST", f"/api/mappings/{created['id']}/retire").status_code == 404


def open_draft(member: Member) -> dict:
    return member.request("GET", "/api/ontology/drafts/open").json()


@pytest.mark.db
def test_draft_reads_carry_the_hash_a_validation_run_must_match(draft_member):
    member, draft = draft_member
    before = open_draft(member)
    assert before["content_hash"] == validate(member, draft)["content_hash"]
    assert save(member, mapping(draft)).status_code == 201
    after = open_draft(member)
    assert after["revision"] == before["revision"]
    assert after["content_hash"] != before["content_hash"]
    assert after["content_hash"] == validate(member, draft)["content_hash"]
