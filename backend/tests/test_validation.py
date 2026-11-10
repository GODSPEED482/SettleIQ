"""Hash-bound ontology validation (task 2.3, AC-ONT-05, AC-ONTO-PUB-02 and 03)."""

import json

import pytest
from rdflib import OWL, RDFS, Graph
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import scoped_tx
from app.domain_packs.manifest import DomainPackManifest, load_domain_packs
from app.semantics import artifacts, store, validation
from packs.finops import validators as finops_validators
from packs.finops.validators import FINOPS
from tests.test_semantics_store import Member


def finance_graph() -> Graph:
    graph = Graph()
    for artifact in [
        artifacts.core_artifact(),
        *artifacts.pack_artifacts(load_domain_packs()["finops"]),
    ]:
        graph.parse(data=json.dumps(artifact.content), format="json-ld")
    return graph


# No database


def test_shipped_finance_terms_pass_the_finance_validators():
    graph = finance_graph()
    assert finops_validators.check_settlement_cardinality(graph) == []
    assert finops_validators.check_required_terms(graph) == []


def test_merging_batch_and_entry_fails():
    graph = finance_graph()
    graph.add((FINOPS.SettlementEntry, RDFS.subClassOf, FINOPS.SettlementBatch))
    assert finops_validators.check_settlement_cardinality(graph)


def test_declaring_batch_and_entry_equivalent_fails():
    graph = finance_graph()
    graph.add((FINOPS.SettlementBatch, OWL.equivalentClass, FINOPS.SettlementEntry))
    assert finops_validators.check_settlement_cardinality(graph)


def test_missing_required_term_is_named():
    assert "Required Finance term is missing: Capture" in finops_validators.check_required_terms(
        Graph()
    )


@pytest.mark.parametrize(
    "node",
    [
        {"@id": "tenant:A", "rdfs:seeAlso": {"@context": "https://evil.example/ctx.jsonld"}},
        {"@id": "tenant:A", "rdfs:seeAlso": [{"nested": {"@context": {"x": "y"}}}]},
        {"@id": "tenant:A", "@context": "https://evil.example/ctx.jsonld"},
    ],
)
def test_context_is_refused_at_any_depth(node):
    with pytest.raises(Exception, match="@context"):
        store._check_extension_graph([node])


def test_a_crashing_validator_fails_closed(monkeypatch):
    def explode(entrypoint):
        raise RuntimeError("boom")

    monkeypatch.setattr(validation, "resolve_entrypoint", explode)
    result = validation._run_pack_validator("v", "packs.x.y:z", Graph())
    assert result.passed is False


def test_unreadable_shapes_fail_closed(tmp_path):
    missing = tmp_path / "missing.ttl"
    assert validation._run_shacl("s", Graph(), missing).passed is False


# Database


@pytest.fixture
def finance_draft() -> tuple[Member, dict]:
    member = Member()
    member.activate("finops")
    return member, member.create_draft().json()


def run_validation(member: Member, draft: dict):
    return member.request("POST", "/api/ontology/validate", json={"draft_id": draft["id"]})


def by_name(body: dict) -> dict[str, dict]:
    return {result["validator"]: result for result in body["results"]}


PAYMENT_WITHOUT_AMOUNT = {"@id": "tenant:p1", "@type": "finops:Payment"}
VALID_CLASS = {"@id": "tenant:VipOrder", "@type": "owl:Class", "rdfs:label": "VIP order",
               "rdfs:subClassOf": "finops:Order"}


@pytest.mark.db
def test_clean_candidate_passes_every_validator(finance_draft):
    member, draft = finance_draft
    body = run_validation(member, draft).json()
    assert body["passed"] is True
    assert set(by_name(body)) == {
        "composition.current", "mappings.comparable", "mappings.terms_exist", "core.shapes",
        "finops.shapes", "finops.settlement_cardinality", "finops.required_terms",
    }


@pytest.mark.db
def test_each_validator_is_reported_separately(finance_draft):
    member, draft = finance_draft
    member.edit(draft, [PAYMENT_WITHOUT_AMOUNT])
    body = run_validation(member, draft).json()
    results = by_name(body)
    assert body["passed"] is False
    assert results["finops.shapes"]["passed"] is False
    assert any("needs an amount" in m for m in results["finops.shapes"]["messages"])
    assert results["core.shapes"]["passed"] is True
    assert results["finops.required_terms"]["passed"] is True


@pytest.mark.db
def test_shacl_failure_applies_to_a_tenant_subclass(finance_draft):
    member, draft = finance_draft
    graph = [
        {"@id": "tenant:VipPayment", "@type": "owl:Class", "rdfs:label": "VIP",
         "rdfs:subClassOf": "finops:Payment"},
        {"@id": "tenant:p1", "@type": "tenant:VipPayment"},
    ]
    member.edit(draft, graph)
    assert run_validation(member, draft).json()["passed"] is False


@pytest.mark.db
def test_pack_validator_failure_blocks_even_when_shacl_passes(finance_draft):
    member, draft = finance_draft
    member.edit(draft, [{"@id": "tenant:Note", "@type": "owl:Class", "rdfs:label": "Note"}])
    original = finops_validators.check_required_terms
    finops_validators.check_required_terms = lambda graph: ["forced failure"]
    try:
        body = run_validation(member, draft).json()
    finally:
        finops_validators.check_required_terms = original
    assert body["passed"] is False
    assert by_name(body)["finops.shapes"]["passed"] is True
    assert by_name(body)["finops.required_terms"]["messages"] == ["forced failure"]


@pytest.mark.db
def test_core_shape_failure_is_reported(finance_draft):
    member, draft = finance_draft
    member.edit(draft, [{"@id": "tenant:Unlabelled", "@type": "owl:Class"}])
    results = by_name(run_validation(member, draft).json())
    assert results["core.shapes"]["passed"] is False


@pytest.mark.db
def test_finance_rules_do_not_run_without_the_finance_pack():
    member = Member()
    member.activate("saas")
    draft = member.create_draft().json()
    member.edit(draft, [PAYMENT_WITHOUT_AMOUNT])
    body = run_validation(member, draft).json()
    assert body["passed"] is True
    assert not [name for name in by_name(body) if name.startswith("finops")]


@pytest.mark.db
def test_any_edit_changes_the_hash(finance_draft):
    member, draft = finance_draft
    first = run_validation(member, draft).json()
    member.edit(draft, [VALID_CLASS])
    second = run_validation(member, draft).json()
    assert first["content_hash"] != second["content_hash"]
    assert second["revision"] == first["revision"] + 1


@pytest.mark.db
def test_identical_content_hashes_identically(finance_draft):
    member, draft = finance_draft
    assert (
        run_validation(member, draft).json()["content_hash"]
        == run_validation(member, draft).json()["content_hash"]
    )


@pytest.mark.db
def test_earlier_run_stops_matching_after_an_edit(finance_draft):
    member, draft = finance_draft
    run = run_validation(member, draft).json()
    member.edit(draft, [VALID_CLASS])
    with scoped_tx(member.tenant) as conn:
        current = validation.load_candidate(conn, draft["id"]).content_hash
    assert current != run["content_hash"]


@pytest.mark.db
def test_activating_a_pack_after_the_draft_blocks_validation():
    member = Member()
    draft = member.create_draft().json()
    member.activate("finops")
    body = run_validation(member, draft).json()
    assert body["passed"] is False
    assert by_name(body)["composition.current"]["passed"] is False


@pytest.mark.db
def test_stale_composition_can_be_replaced_by_abandoning_the_draft():
    member = Member()
    draft = member.create_draft().json()
    member.activate("finops")
    response = member.request("POST", f"/api/ontology/drafts/{draft['id']}/abandon")
    assert response.status_code == 200
    fresh = member.create_draft().json()
    assert run_validation(member, fresh).json()["passed"] is True


@pytest.mark.db
def test_abandoned_draft_cannot_be_edited_or_validated(finance_draft):
    member, draft = finance_draft
    member.request("POST", f"/api/ontology/drafts/{draft['id']}/abandon")
    assert member.edit(draft, [VALID_CLASS]).status_code == 409
    assert run_validation(member, draft).status_code == 409


@pytest.mark.db
def test_abandoning_twice_fails_closed(finance_draft):
    member, draft = finance_draft
    path = f"/api/ontology/drafts/{draft['id']}/abandon"
    assert member.request("POST", path).status_code == 200
    assert member.request("POST", path).status_code == 409


@pytest.mark.db
def test_uninstalled_active_pack_fails_closed(finance_draft, monkeypatch):
    member, draft = finance_draft
    shipped = load_domain_packs()
    monkeypatch.setattr(validation, "load_domain_packs", lambda: {"saas": shipped["saas"]})
    body = run_validation(member, draft).json()
    assert body["passed"] is False
    assert by_name(body)["finops"]["messages"] == ["This pack is not installed."]


@pytest.mark.db
def test_every_active_packs_validators_run(finance_draft, monkeypatch):
    member, draft = finance_draft
    shipped = load_domain_packs()
    extra = DomainPackManifest(
        **shipped["finops"].model_dump()
        | {"validators": [{"id": "finops.forced", "entrypoint": "packs.finops.validators:nope"}]}
    )
    monkeypatch.setattr(validation, "load_domain_packs", lambda: shipped | {"finops": extra})
    body = run_validation(member, draft).json()
    assert body["passed"] is False
    assert by_name(body)["finops.forced"]["passed"] is False


@pytest.mark.db
def test_run_is_recorded_and_audited(finance_draft):
    member, draft = finance_draft
    body = run_validation(member, draft).json()
    with scoped_tx(member.tenant) as conn:
        run = conn.execute(
            text("SELECT content_hash, passed, draft_revision FROM ontology_validation_runs")
        ).one()
        audit = conn.execute(
            text("SELECT detail FROM audit_logs WHERE action = 'ontology.validated'")
        ).one()
    assert tuple(run) == (body["content_hash"], True, draft["revision"])
    assert audit.detail["validation_run_id"] == body["validation_run_id"]


@pytest.mark.db
@pytest.mark.parametrize(
    "statement",
    ["UPDATE ontology_validation_runs SET passed = true", "DELETE FROM ontology_validation_runs"],
)
def test_runs_are_append_only(finance_draft, statement):
    member, draft = finance_draft
    run_validation(member, draft)
    with pytest.raises(DBAPIError, match="permission denied"):
        with scoped_tx(member.tenant) as conn:
            conn.execute(text(statement))


@pytest.mark.db
def test_validating_another_tenants_draft_is_a_404(finance_draft):
    _, draft = finance_draft
    assert run_validation(Member("Other"), draft).status_code == 404


@pytest.mark.db
def test_nested_remote_context_is_refused_before_it_is_stored(finance_draft):
    member, draft = finance_draft
    response = member.edit(
        draft, [{"@id": "tenant:A", "rdfs:seeAlso": {"@context": "https://evil.example/c"}}]
    )
    assert response.status_code == 422
