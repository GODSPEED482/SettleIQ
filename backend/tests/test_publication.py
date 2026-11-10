"""Ontology publication through the real route (POL-12, AC-ONTO-PUB-01..05, AC-ONT-05, 06)."""

import json
import threading
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.authz.guard import authenticate
from app.db import scoped_tx
from app.domain_packs.manifest import DomainPackManifest, load_domain_packs
from app.semantics import mappings, publication
from packs.finops import validators as finops_validators
from tests.test_mappings import authority, mapping
from tests.test_semantics_store import Member, app


@pytest.fixture
def finance_draft() -> tuple[Member, dict]:
    member = Member()
    member.activate("finops")
    return member, member.create_draft().json()


def validate(member: Member, draft: dict) -> dict:
    return member.request("POST", "/api/ontology/validate", json={"draft_id": draft["id"]}).json()


def publish_body(draft: dict, run: dict, **overrides) -> dict:
    return {
        "draft_id": draft["id"],
        "validation_run_id": run["validation_run_id"],
        "confirm_publish": True,
    } | overrides


def post_publish(member: Member, body: dict):
    return member.request("POST", "/api/ontology/publish", json=body)


def published_count(member: Member) -> int:
    with scoped_tx(member.tenant) as conn:
        return conn.execute(text("SELECT count(*) FROM ontologies")).scalar_one()


@pytest.mark.db
@pytest.mark.parametrize("value", [False, "true", 1, None])
def test_anything_but_an_explicit_true_is_refused(finance_draft, value):
    member, draft = finance_draft
    run = validate(member, draft)
    response = post_publish(member, publish_body(draft, run, confirm_publish=value))
    assert response.status_code == 422
    assert published_count(member) == 0


@pytest.mark.db
def test_a_missing_confirmation_is_refused(finance_draft):
    member, draft = finance_draft
    body = publish_body(draft, validate(member, draft))
    del body["confirm_publish"]
    assert post_publish(member, body).status_code == 422
    assert published_count(member) == 0


@pytest.mark.db
@pytest.mark.parametrize("field", ["tenant_id", "published_by", "content_hash", "version"])
def test_the_body_cannot_carry_server_derived_fields(finance_draft, field):
    member, draft = finance_draft
    body = publish_body(draft, validate(member, draft), **{field: str(uuid4())})
    assert post_publish(member, body).status_code == 422


def publish(member: Member, draft: dict):
    return post_publish(member, publish_body(draft, validate(member, draft)))


def draft_status(member: Member, draft: dict) -> str:
    with scoped_tx(member.tenant) as conn:
        return conn.execute(
            text("SELECT status FROM ontology_drafts WHERE id = :id"), {"id": draft["id"]}
        ).scalar_one()


def audits(member: Member, action: str) -> list[dict]:
    with scoped_tx(member.tenant) as conn:
        return list(
            conn.execute(
                text("SELECT detail FROM audit_logs WHERE action = :a ORDER BY created_at"),
                {"a": action},
            ).scalars()
        )


def add_source(member: Member) -> str:
    source_id = str(uuid4())
    with scoped_tx(member.tenant) as conn:
        conn.execute(
            text(
                """
                INSERT INTO data_sources (id, tenant_id, connector_id, connector_version,
                    source_type, protocol, display_name, created_by)
                VALUES (:id, :tenant, 'paypal', '1.0.0', 'payment_provider', 'http', 'x', :user)
                """
            ),
            {"id": source_id, "tenant": member.tenant, "user": member.principal.id},
        )
    return source_id


def assert_refused(response, code: str, member: Member) -> None:
    assert response.status_code == 409
    assert response.json()["error"]["code"] == code
    assert audits(member, "ontology.publish.refused")[-1]["reason"] == code
    assert audits(member, "ontology.publish.refused")[-1]["outcome"] == "REFUSED"


VALID_CLASS = {"@id": "tenant:VipOrder", "@type": "owl:Class", "rdfs:label": "VIP order",
               "rdfs:subClassOf": "finops:Order"}
PAYMENT_WITHOUT_AMOUNT = {"@id": "tenant:p1", "@type": "finops:Payment"}


# AC-ONTO-PUB-04, AC-ONT-06


@pytest.mark.db
def test_publish_creates_the_first_version_and_closes_the_draft(finance_draft):
    member, draft = finance_draft
    run = validate(member, draft)
    response = post_publish(member, publish_body(draft, run))
    assert response.status_code == 200
    body = response.json()
    assert (body["version"], body["content_hash"]) == (1, run["content_hash"])
    assert draft_status(member, draft) == "PUBLISHED"
    active = member.request("GET", "/api/ontology").json()
    assert (active["version"], active["content_hash"]) == (1, run["content_hash"])


@pytest.mark.db
def test_publish_audits_the_full_record(finance_draft):
    member, draft = finance_draft
    run = validate(member, draft)
    body = post_publish(member, publish_body(draft, run)).json()
    with scoped_tx(member.tenant) as conn:
        row = conn.execute(
            text(
                "SELECT tenant_id, principal_id, detail FROM audit_logs "
                "WHERE action = 'ontology.published'"
            )
        ).one()
    assert (row.tenant_id, row.principal_id) == (member.tenant, member.principal.id)
    detail = row.detail
    assert detail["outcome"] == "PUBLISHED"
    assert detail["version"] == 1
    assert detail["base_version"] == 0
    assert detail["draft_id"] == draft["id"]
    assert detail["draft_revision"] == draft["revision"]
    assert detail["content_hash"] == run["content_hash"]
    assert detail["validation_run_id"] == run["validation_run_id"]
    assert detail["publisher"] == str(member.principal.id)
    assert detail["confirmed_at"] == body["published_at"]
    assert [item["namespace"] for item in detail["composition"]] == ["core", "finops"]


@pytest.mark.db
def test_publish_stores_pack_and_tenant_relationships(finance_draft):
    member, draft = finance_draft
    relationship = {"@id": "tenant:flaggedBy", "@type": "owl:ObjectProperty",
                    "rdfs:label": "flagged by", "rdfs:domain": "finops:Order",
                    "rdfs:range": "finops:Customer"}
    edited = member.edit(draft, [relationship]).json()
    body = publish(member, edited).json()
    assert body["relationships"] == 12
    with scoped_tx(member.tenant) as conn:
        rows = {
            row.iri: row
            for row in conn.execute(
                text("SELECT iri, domain_iri, range_iri, definition FROM ontology_relationships")
            )
        }
    assert len(rows) == 12
    capture = rows["urn:settleiq:pack:finops#generatesCapture"]
    assert capture.domain_iri == "urn:settleiq:pack:finops#Payment"
    assert capture.range_iri == "urn:settleiq:pack:finops#Capture"
    tenant_iri = f"urn:settleiq:tenant:{member.tenant}#flaggedBy"
    assert rows[tenant_iri].definition == {"label": "flagged by"}


@pytest.mark.db
def test_a_later_draft_publishes_the_next_version(finance_draft):
    member, draft = finance_draft
    assert publish(member, draft).json()["version"] == 1
    second = member.create_draft().json()
    assert second["base_version"] == 1
    assert publish(member, second).json()["version"] == 2


@pytest.mark.db
def test_a_new_draft_starts_from_the_published_version(finance_draft):
    member, draft = finance_draft
    source = add_source(member)
    edited = member.edit(draft, [VALID_CLASS]).json()
    member.request("POST", "/api/mappings", json=mapping(draft["id"], source_id=source))
    member.request("POST", "/api/authority", json=authority(draft["id"], source_id=source))
    first = publish(member, edited).json()

    successor = member.create_draft().json()
    assert successor["base_version"] == 1
    assert successor["tenant_extension"]["@graph"] == [VALID_CLASS]
    params = {"draft_id": successor["id"]}
    bindings = member.request("GET", "/api/mappings", params=params).json()
    assertions = member.request("GET", "/api/authority", params=params).json()
    assert [(b["source_id"], b["version"]) for b in bindings] == [(source, 1)]
    assert [(a["source_id"], a["version"]) for a in assertions] == [(source, 1)]
    # Nothing changed, so the successor is exactly the version it came from.
    assert validate(member, successor)["content_hash"] == first["content_hash"]


@pytest.mark.db
def test_a_failure_part_way_leaves_nothing_behind(finance_draft, monkeypatch):
    member, draft = finance_draft
    run = validate(member, draft)

    def explode(*args, **kwargs):
        raise RuntimeError("audit down")

    monkeypatch.setattr(publication, "write_audit", explode)
    with pytest.raises(RuntimeError, match="audit down"):
        post_publish(member, publish_body(draft, run))
    assert published_count(member) == 0
    assert draft_status(member, draft) == "OPEN"
    with scoped_tx(member.tenant) as conn:
        assert conn.execute(text("SELECT count(*) FROM ontology_relationships")).scalar() == 0


# AC-ONTO-PUB-02, AC-ONT-05


@pytest.mark.db
def test_a_failed_run_is_refused(finance_draft):
    member, draft = finance_draft
    edited = member.edit(draft, [PAYMENT_WITHOUT_AMOUNT]).json()
    run = validate(member, edited)
    assert run["passed"] is False
    response = post_publish(member, publish_body(edited, run))
    assert_refused(response, "VALIDATION_NOT_PASSED", member)
    assert published_count(member) == 0
    assert draft_status(member, edited) == "OPEN"


@pytest.mark.db
def test_a_pack_validator_failure_blocks_publication(finance_draft):
    member, draft = finance_draft
    original = finops_validators.check_required_terms
    finops_validators.check_required_terms = lambda graph: ["forced failure"]
    try:
        run = validate(member, draft)
    finally:
        finops_validators.check_required_terms = original
    response = post_publish(member, publish_body(draft, run))
    assert_refused(response, "VALIDATION_NOT_PASSED", member)


@pytest.mark.db
def test_an_unknown_run_is_a_404(finance_draft):
    member, draft = finance_draft
    body = publish_body(draft, {"validation_run_id": str(uuid4())})
    assert post_publish(member, body).status_code == 404


@pytest.mark.db
def test_a_run_for_another_draft_is_a_404(finance_draft):
    member, draft = finance_draft
    old_run = validate(member, draft)
    member.request("POST", f"/api/ontology/drafts/{draft['id']}/abandon")
    fresh = member.create_draft().json()
    assert post_publish(member, publish_body(fresh, old_run)).status_code == 404


# AC-ONTO-PUB-03


@pytest.mark.db
def test_an_edit_after_validation_invalidates_the_run(finance_draft):
    member, draft = finance_draft
    run = validate(member, draft)
    edited = member.edit(draft, [VALID_CLASS]).json()
    response = post_publish(member, publish_body(edited, run))
    assert_refused(response, "VALIDATION_STALE", member)
    assert published_count(member) == 0


@pytest.mark.db
def test_a_new_run_after_the_edit_can_publish(finance_draft):
    member, draft = finance_draft
    validate(member, draft)
    edited = member.edit(draft, [VALID_CLASS]).json()
    assert publish(member, edited).status_code == 200


@pytest.mark.db
def test_reverting_an_edit_does_not_revive_the_run(finance_draft):
    member, draft = finance_draft
    run = validate(member, draft)
    edited = member.edit(draft, [VALID_CLASS]).json()
    reverted = member.edit(edited, []).json()
    response = post_publish(member, publish_body(reverted, run))
    assert_refused(response, "VALIDATION_STALE", member)


@pytest.mark.db
def test_a_mapping_change_after_validation_invalidates_the_run(finance_draft):
    member, draft = finance_draft
    source = add_source(member)
    run = validate(member, draft)
    saved = member.request("POST", "/api/mappings", json=mapping(draft["id"], source_id=source))
    assert saved.status_code == 201
    response = post_publish(member, publish_body(draft, run))
    assert_refused(response, "VALIDATION_STALE", member)


@pytest.mark.db
def test_an_authority_change_after_validation_invalidates_the_run(finance_draft):
    member, draft = finance_draft
    source = add_source(member)
    run = validate(member, draft)
    saved = member.request("POST", "/api/authority", json=authority(draft["id"], source_id=source))
    assert saved.status_code == 201
    response = post_publish(member, publish_body(draft, run))
    assert_refused(response, "VALIDATION_STALE", member)


@pytest.mark.db
def test_activating_a_pack_after_validation_invalidates_the_run():
    member = Member()
    draft = member.create_draft().json()
    run = validate(member, draft)
    assert run["passed"] is True
    member.activate("finops")
    response = post_publish(member, publish_body(draft, run))
    assert_refused(response, "VALIDATION_STALE", member)


@pytest.mark.db
def test_a_validators_only_pack_activated_after_validation_invalidates_the_run(
    finance_draft, monkeypatch
):
    member, draft = finance_draft
    run = validate(member, draft)
    member.activate("saas")
    shipped = load_domain_packs()
    assert shipped["saas"].ontology_definitions == []
    with_validator = DomainPackManifest(
        **shipped["saas"].model_dump()
        | {"validators": [
            {"id": "saas.check", "entrypoint": "packs.saas_subscription.validators:check"}
        ]}
    )
    monkeypatch.setattr(
        publication, "load_domain_packs", lambda: shipped | {"saas": with_validator}
    )
    response = post_publish(member, publish_body(draft, run))
    assert_refused(response, "VALIDATION_STALE", member)


# AC-ONTO-PUB-05


@pytest.mark.db
def test_publishing_twice_fails_closed(finance_draft):
    member, draft = finance_draft
    run = validate(member, draft)
    assert post_publish(member, publish_body(draft, run)).status_code == 200
    response = post_publish(member, publish_body(draft, run))
    assert_refused(response, "STALE_DRAFT", member)
    assert published_count(member) == 1


@pytest.mark.db
def test_a_stale_base_version_fails_closed_and_overwrites_nothing(finance_draft):
    member, draft = finance_draft
    run = validate(member, draft)
    composition = [{k: a[k] for k in ("layer", "namespace", "version", "content_hash")}
                   for a in draft["artifacts"]]
    with scoped_tx(member.tenant) as conn:
        conn.execute(
            text(
                """
                INSERT INTO ontologies (id, tenant_id, version, composition, tenant_extension,
                    content_hash, draft_id, published_by)
                VALUES (:id, :t, 1, CAST(:c AS jsonb), '{}', :h, :d, :u)
                """
            ),
            {"id": uuid4(), "t": member.tenant, "c": json.dumps(composition), "h": "a" * 64,
             "d": draft["id"], "u": member.principal.id},
        )
    response = post_publish(member, publish_body(draft, run))
    assert_refused(response, "STALE_BASE_VERSION", member)
    active = member.request("GET", "/api/ontology").json()
    assert (active["version"], active["content_hash"]) == (1, "a" * 64)
    assert draft_status(member, draft) == "OPEN"


def race(member: Member, *calls) -> list[int]:
    """Run each call at the same moment and return their status codes."""
    barrier = threading.Barrier(len(calls))
    results: list[int] = []

    def attempt(call):
        barrier.wait()
        results.append(call().status_code)

    app.dependency_overrides[authenticate] = lambda: member.principal
    try:
        threads = [threading.Thread(target=attempt, args=(call,)) for call in calls]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        app.dependency_overrides.clear()
    return results


@pytest.mark.db
def test_concurrent_publishes_make_one_version(finance_draft):
    member, draft = finance_draft
    body = publish_body(draft, validate(member, draft))

    def attempt():
        return member.client.post("/api/ontology/publish", json=body)

    assert sorted(race(member, attempt, attempt, attempt, attempt)) == [200, 409, 409, 409]
    assert published_count(member) == 1


@pytest.mark.db
@pytest.mark.parametrize("attempt_number", range(5))
def test_publish_racing_a_mapping_change_never_deadlocks(finance_draft, attempt_number):
    member, draft = finance_draft
    source = add_source(member)
    body = publish_body(draft, validate(member, draft))
    change = mapping(draft["id"], source_id=source)
    results = race(
        member,
        lambda: member.client.post("/api/ontology/publish", json=body),
        lambda: member.client.post("/api/mappings", json=change),
    )
    # Whichever commits first wins, and the loser is refused rather than failing.
    assert sorted(results) in ([200, 409], [201, 409])


@pytest.mark.db
@pytest.mark.parametrize("attempt_number", range(5))
def test_publish_racing_an_edit_has_one_winner(finance_draft, attempt_number):
    member, draft = finance_draft
    body = publish_body(draft, validate(member, draft))
    edit = {"expected_revision": draft["revision"], "graph": [VALID_CLASS]}
    results = race(
        member,
        lambda: member.client.post("/api/ontology/publish", json=body),
        lambda: member.client.patch(f"/api/ontology/drafts/{draft['id']}", json=edit),
    )
    assert sorted(results) == [200, 409]


# Gate 2 exit: versions are immutable and pinned, and two sources can bind one property


def version_snapshot(member: Member, version: int) -> dict:
    with scoped_tx(member.tenant) as conn:
        row = conn.execute(
            text("SELECT * FROM ontologies WHERE version = :v"), {"v": version}
        ).one()
        relationships = conn.execute(
            text(
                "SELECT iri, domain_iri, range_iri, definition FROM ontology_relationships "
                "WHERE ontology_id = :o ORDER BY iri"
            ),
            {"o": row.id},
        ).all()
        return {
            "row": tuple(row),
            "relationships": [tuple(item) for item in relationships],
            "mappings": mappings.current_mappings(conn, row.draft_id),
            "authority": mappings.current_authority(conn, row.draft_id),
        }


@pytest.mark.db
def test_an_earlier_version_stays_pinned_after_a_newer_one(finance_draft):
    member, draft = finance_draft
    source = add_source(member)
    edited = member.edit(draft, [VALID_CLASS]).json()
    member.request("POST", "/api/mappings", json=mapping(draft["id"], source_id=source))
    member.request("POST", "/api/authority", json=authority(draft["id"], source_id=source))
    first = publish(member, edited).json()
    pinned = version_snapshot(member, 1)

    successor = member.create_draft().json()
    other_class = {"@id": "tenant:Gold", "@type": "owl:Class", "rdfs:label": "Gold"}
    changed = member.edit(successor, [VALID_CLASS, other_class]).json()
    member.request(
        "POST", "/api/mappings", json=mapping(successor["id"], source_id=source, nullable=True)
    )
    params = {"draft_id": successor["id"]}
    assertion = member.request("GET", "/api/authority", params=params).json()[0]
    member.request("POST", f"/api/authority/{assertion['id']}/retire")
    second = publish(member, changed).json()

    assert second["version"] == 2
    assert second["content_hash"] != first["content_hash"]
    assert version_snapshot(member, 1) == pinned
    latest = version_snapshot(member, 2)
    assert latest["mappings"][0]["nullable"] is True
    assert latest["authority"] == []
    assert member.request("GET", "/api/ontology").json()["version"] == 2


@pytest.mark.db
def test_the_routes_refuse_to_change_a_published_draft(finance_draft):
    member, draft = finance_draft
    source = add_source(member)
    created = member.request(
        "POST", "/api/mappings", json=mapping(draft["id"], source_id=source)
    ).json()
    publish(member, draft)
    attempts = [
        member.request("POST", "/api/mappings", json=mapping(draft["id"], source_id=source)),
        member.request("POST", "/api/authority", json=authority(draft["id"], source_id=source)),
        member.request("POST", f"/api/mappings/{created['id']}/retire"),
        member.edit(draft, [VALID_CLASS]),
        member.request("POST", f"/api/ontology/drafts/{draft['id']}/abandon"),
    ]
    assert [attempt.status_code for attempt in attempts] == [409] * 5


@pytest.mark.db
@pytest.mark.parametrize(
    "statement",
    ["UPDATE ontology_relationships SET iri = 'x'", "DELETE FROM ontology_relationships"],
)
def test_published_relationships_are_immutable(finance_draft, statement):
    member, draft = finance_draft
    publish(member, draft)
    with pytest.raises(DBAPIError, match="permission denied"):
        with scoped_tx(member.tenant) as conn:
            conn.execute(text(statement))


@pytest.mark.db
def test_two_sources_to_one_property_publish_as_separate_bindings(finance_draft):
    member, draft = finance_draft
    first, second = add_source(member), add_source(member)
    for body in (
        mapping(draft["id"], source_id=first),
        mapping(draft["id"], source_id=second, external_object="payments"),
    ):
        assert member.request("POST", "/api/mappings", json=body).status_code == 201
    assert publish(member, draft).status_code == 200
    bindings = version_snapshot(member, 1)["mappings"]
    assert {b["source_id"] for b in bindings} == {first, second}
    assert {b["property_iri"] for b in bindings} == {"finops:amount"}
    assert len(bindings) == 2


@pytest.mark.db
def test_incomparable_sources_on_one_property_cannot_publish(finance_draft):
    member, draft = finance_draft
    first, second = add_source(member), add_source(member)
    member.request("POST", "/api/mappings", json=mapping(draft["id"], source_id=first))
    member.request(
        "POST", "/api/mappings",
        json=mapping(draft["id"], source_id=second, value_basis="fulfilled"),
    )
    response = publish(member, draft)
    assert_refused(response, "VALIDATION_NOT_PASSED", member)
    assert published_count(member) == 0


# Tenant isolation


@pytest.mark.db
def test_another_tenant_cannot_publish_a_draft(finance_draft):
    member, draft = finance_draft
    body = publish_body(draft, validate(member, draft))
    other = Member("Other")
    assert post_publish(other, body).status_code == 404
    assert draft_status(member, draft) == "OPEN"


@pytest.mark.db
def test_another_tenants_run_cannot_validate_a_draft(finance_draft):
    member, draft = finance_draft
    foreign_run = validate(member, draft)
    other = Member("Other")
    other.activate("finops")
    other_draft = other.create_draft().json()
    assert post_publish(other, publish_body(other_draft, foreign_run)).status_code == 404
    assert published_count(other) == 0
