"""Pack activation and ontology composition through the real routes (task 2.1, spec 5.1)."""

import json
import threading
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from rdflib import OWL, RDF, RDFS, Graph, Namespace
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.authz.access import Principal, PrincipalKind
from app.authz.guard import TENANT_HEADER, authenticate
from app.db import global_tx, scoped_tx
from app.domain_packs.manifest import DomainPackManifest, load_domain_packs
from app.main import create_app
from app.semantics import artifacts, store
from app.semantics.hashing import content_hash
from app.tenancy.creation import create_tenant

FINOPS = Namespace("urn:settleiq:pack:finops#")
CORE = Namespace("urn:settleiq:core#")
app = create_app()


# No database


def graph_of(artifact: artifacts.Artifact) -> Graph:
    return Graph().parse(data=json.dumps(artifact.content), format="json-ld")


def finops_graph() -> Graph:
    return graph_of(artifacts.pack_artifacts(load_domain_packs()["finops"])[0])


def test_hash_ignores_key_order():
    assert content_hash({"a": 1, "b": [2]}) == content_hash({"b": [2], "a": 1})
    assert content_hash({"a": 1}) != content_hash({"a": 2})


def test_core_declares_the_platform_classes():
    classes = set(graph_of(artifacts.core_artifact()).subjects(RDF.type, OWL.Class))
    for name in ("Tenant", "Actor", "DataSource", "SourceRecord", "EvidenceArtifact",
                 "DomainEntity", "DomainEvent", "Relationship", "Policy", "WorkflowCase",
                 "Action", "Provenance"):
        assert CORE[name] in classes


def test_core_has_no_finance_terms():
    labels = {str(s) for s in graph_of(artifacts.core_artifact()).subjects()}
    assert not {label for label in labels if "Payment" in label or "Settlement" in label}


def test_finance_pack_declares_the_spec_classes():
    classes = set(finops_graph().subjects(RDF.type, OWL.Class))
    for name in ("Customer", "Order", "Payment", "Authorization", "Capture", "Refund",
                 "SettlementBatch", "SettlementEntry", "BankTransaction", "ProcessingFee",
                 "Chargeback", "AccountingEntry", "PaymentProvider", "ReconciliationCase"):
        assert FINOPS[name] in classes


def test_settlement_batch_and_entry_are_distinct():
    assert FINOPS.SettlementBatch != FINOPS.SettlementEntry
    assert (FINOPS.SettlementEntry, RDFS.subClassOf, FINOPS.SettlementBatch) not in finops_graph()


@pytest.mark.parametrize(
    ("name", "domain", "range_"),
    [
        ("generatesCapture", FINOPS.Payment, FINOPS.Capture),
        ("refundsCapture", FINOPS.Refund, FINOPS.Capture),
        ("settledThrough", FINOPS.SettlementBatch, FINOPS.BankTransaction),
        ("recordedIn", FINOPS.FinancialEntry, CORE.DataSource),
    ],
)
def test_relationships_carry_domain_and_range(name, domain, range_):
    graph = finops_graph()
    assert (FINOPS[name], RDFS.domain, domain) in graph
    assert (FINOPS[name], RDFS.range, range_) in graph


def test_every_relationship_has_a_declared_domain_and_range():
    graph = finops_graph()
    properties = list(graph.subjects(RDF.type, OWL.ObjectProperty))
    assert len(properties) == 11
    for prop in properties:
        assert graph.value(prop, RDFS.domain) is not None
        assert graph.value(prop, RDFS.range) is not None


def test_pack_term_outside_its_namespace_is_refused():
    content = {"@context": {"finops": artifacts.pack_iri("finops")},
               "@graph": [{"@id": "core:Tenant"}]}
    with pytest.raises(artifacts.ArtifactError, match="outside namespace"):
        artifacts._check_namespace(content, "finops", artifacts.pack_iri("finops"), "t")


def test_pack_prefix_must_map_to_its_own_iri():
    content = {"@context": {"finops": "urn:settleiq:core#"}, "@graph": []}
    with pytest.raises(artifacts.ArtifactError, match="must map"):
        artifacts._check_namespace(content, "finops", artifacts.pack_iri("finops"), "t")


@pytest.mark.parametrize(
    "node", [{"@id": "core:Tenant"}, {"@id": "finops:Order"}, {"rdfs:label": "no id"}]
)
def test_extension_cannot_redefine_inherited_terms(node):
    with pytest.raises(Exception, match="tenant prefix"):
        store._check_extension_graph([node])


def test_extension_node_cannot_bring_its_own_context():
    with pytest.raises(Exception, match="@context"):
        store._check_extension_graph([{"@id": "tenant:X", "@context": {"tenant": "urn:evil#"}}])


# Database


def make_user() -> UUID:
    user_id = uuid4()
    with global_tx() as conn:
        conn.execute(
            text("INSERT INTO users (id, email, email_verified_at) VALUES (:i, :e, now())"),
            {"i": user_id, "e": f"{user_id}@example.com"},
        )
    return user_id


class Member:
    def __init__(self, name: str = "T") -> None:
        self.principal = Principal(make_user(), PrincipalKind.HUMAN, email_verified=True)
        self.tenant = create_tenant(self.principal, name, [])
        self.client = TestClient(app, headers={TENANT_HEADER: str(self.tenant)})

    def request(self, method: str, path: str, **kwargs):
        app.dependency_overrides[authenticate] = lambda: self.principal
        try:
            return self.client.request(method, path, **kwargs)
        finally:
            app.dependency_overrides.clear()

    def activate(self, namespace: str):
        return self.request(
            "POST", "/api/domain-packs", json={"namespace": namespace, "action": "ACTIVATE"}
        )

    def disable(self, namespace: str):
        return self.request(
            "POST", "/api/domain-packs", json={"namespace": namespace, "action": "DISABLE"}
        )

    def create_draft(self):
        return self.request("POST", "/api/ontology/drafts")

    def edit(self, draft: dict, graph: list[dict], revision: int | None = None):
        return self.request(
            "PATCH",
            f"/api/ontology/drafts/{draft['id']}",
            json={"expected_revision": revision or draft["revision"], "graph": graph},
        )


@pytest.fixture
def member() -> Member:
    return Member()


def statuses(member: Member) -> dict[str, str]:
    body = member.request("GET", "/api/domain-packs").json()
    return {pack["namespace"]: pack["status"] for pack in body}


@pytest.mark.db
def test_shipped_packs_start_available_and_are_not_activated_by_default(member):
    assert statuses(member) == {"finops": "AVAILABLE", "saas": "AVAILABLE"}


@pytest.mark.db
def test_two_compatible_packs_can_be_active_together(member):
    assert member.activate("finops").status_code == 200
    assert member.activate("saas").status_code == 200
    assert statuses(member) == {"finops": "ACTIVE", "saas": "ACTIVE"}


@pytest.mark.db
def test_unknown_pack_is_a_404(member):
    assert member.activate("nope").status_code == 404


@pytest.mark.db
def test_activation_is_audited(member):
    member.activate("finops")
    with scoped_tx(member.tenant) as conn:
        row = conn.execute(
            text("SELECT detail FROM audit_logs WHERE action = 'domain_pack.activated'")
        ).one()
    assert row.detail["namespace"] == "finops"


def _with_conflict(monkeypatch) -> None:
    shipped = load_domain_packs()
    conflicted = DomainPackManifest(**shipped["saas"].model_dump() | {"conflicts": ["finops"]})
    monkeypatch.setattr(store, "load_domain_packs", lambda: shipped | {"saas": conflicted})


@pytest.mark.db
def test_conflicting_pack_is_refused_and_changes_nothing(member, monkeypatch):
    member.activate("finops")
    _with_conflict(monkeypatch)
    response = member.activate("saas")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PACK_NOT_ACTIVATABLE"
    assert statuses(member)["saas"] == "AVAILABLE"


@pytest.mark.db
def test_pack_with_dependents_cannot_be_disabled(member, monkeypatch):
    shipped = load_domain_packs()
    dependent = DomainPackManifest(
        **shipped["saas"].model_dump()
        | {"dependencies": [{"namespace": "finops", "version": ">=0.1.0"}]}
    )
    monkeypatch.setattr(store, "load_domain_packs", lambda: shipped | {"saas": dependent})
    member.activate("finops")
    member.activate("saas")
    assert member.disable("finops").status_code == 409
    assert member.disable("saas").status_code == 200
    assert member.disable("finops").status_code == 200


@pytest.mark.db
def test_disabling_an_inactive_pack_is_a_404(member):
    assert member.disable("finops").status_code == 404


@pytest.mark.db
def test_composition_keeps_core_pack_and_extension_separate(member):
    member.activate("finops")
    draft = member.create_draft().json()
    layers = [(a["layer"], a["namespace"]) for a in draft["artifacts"]]
    assert layers == [("CORE", "core"), ("PACK", "finops")]
    assert draft["tenant_extension"]["@graph"] == []
    assert {"tenant", "core", "finops"} <= set(draft["tenant_extension"]["@context"])


@pytest.mark.db
def test_draft_for_a_tenant_without_finance_has_no_finance_terms(member):
    member.activate("saas")
    draft = member.create_draft().json()
    assert [a["namespace"] for a in draft["artifacts"]] == ["core"]
    assert "finops" not in draft["tenant_extension"]["@context"]


@pytest.mark.db
def test_only_one_open_draft(member):
    assert member.create_draft().status_code == 201
    response = member.create_draft()
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DRAFT_EXISTS"


@pytest.mark.db
def test_concurrent_draft_creation_makes_one_draft(member):
    results = []

    def attempt():
        results.append(member.client.post("/api/ontology/drafts").status_code)

    app.dependency_overrides[authenticate] = lambda: member.principal
    try:
        threads = [threading.Thread(target=attempt) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        app.dependency_overrides.clear()
    assert sorted(results) == [201, 409, 409, 409]


@pytest.mark.db
def test_open_draft_can_be_read_back(member):
    created = member.create_draft().json()
    assert member.request("GET", "/api/ontology/drafts/open").json() == created


@pytest.mark.db
def test_no_open_draft_is_a_404(member):
    assert member.request("GET", "/api/ontology/drafts/open").status_code == 404


@pytest.mark.db
def test_editing_bumps_the_revision_and_keeps_tenant_terms(member):
    draft = member.create_draft().json()
    term = {"@id": "tenant:VipOrder", "@type": "owl:Class", "rdfs:label": "VIP order"}
    response = member.edit(draft, [term])
    assert response.status_code == 200
    assert response.json()["revision"] == draft["revision"] + 1
    assert response.json()["tenant_extension"]["@graph"] == [term]


@pytest.mark.db
def test_stale_edit_fails_closed(member):
    draft = member.create_draft().json()
    assert member.edit(draft, [{"@id": "tenant:A"}]).status_code == 200
    response = member.edit(draft, [{"@id": "tenant:B"}])
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "STALE_DRAFT"
    current = member.request("GET", "/api/ontology/drafts/open").json()
    assert current["tenant_extension"]["@graph"] == [{"@id": "tenant:A"}]


@pytest.mark.db
def test_edit_cannot_override_an_inherited_term(member):
    draft = member.create_draft().json()
    assert member.edit(draft, [{"@id": "core:Tenant"}]).status_code == 422


@pytest.mark.db
def test_edit_body_cannot_carry_a_context_or_tenant(member):
    draft = member.create_draft().json()
    response = member.request(
        "PATCH",
        f"/api/ontology/drafts/{draft['id']}",
        json={"expected_revision": 1, "graph": [], "tenant_id": str(uuid4())},
    )
    assert response.status_code == 422


@pytest.mark.db
def test_ontology_is_empty_until_something_is_published(member):
    assert member.request("GET", "/api/ontology").json()["version"] is None


@pytest.mark.db
def test_active_ontology_is_the_highest_published_version(member):
    member.activate("finops")
    draft = member.create_draft().json()
    composition = [{k: a[k] for k in ("layer", "namespace", "version", "content_hash")}
                   for a in draft["artifacts"]]
    with scoped_tx(member.tenant) as conn:
        for version in (1, 2):
            conn.execute(
                text(
                    """
                    INSERT INTO ontologies (id, tenant_id, version, composition,
                        tenant_extension, content_hash, draft_id, published_by)
                    VALUES (:id, :t, :v, CAST(:c AS jsonb), '{}', :h, :d, :u)
                    """
                ),
                {
                    "id": uuid4(), "t": member.tenant, "v": version,
                    "c": json.dumps(composition), "h": "a" * 64,
                    "d": draft["id"], "u": member.principal.id,
                },
            )
    body = member.request("GET", "/api/ontology").json()
    assert body["version"] == 2
    assert [a["namespace"] for a in body["artifacts"]] == ["core", "finops"]


@pytest.mark.db
@pytest.mark.parametrize("statement", ["UPDATE ontology_artifacts SET version = 'x'",
                                       "DELETE FROM ontology_artifacts",
                                       "UPDATE ontologies SET version = 9",
                                       "DELETE FROM ontologies"])
def test_artifacts_and_published_versions_are_immutable(member, statement):
    with pytest.raises(DBAPIError, match="permission denied"):
        with scoped_tx(member.tenant) as conn:
            conn.execute(text(statement))


@pytest.mark.db
def test_runtime_role_cannot_change_the_pack_catalogue():
    with pytest.raises(DBAPIError, match="permission denied"):
        with global_tx() as conn:
            conn.execute(text("UPDATE domain_packs SET version = 'x'"))


@pytest.mark.db
def test_tenants_do_not_see_each_others_composition(member):
    other = Member("Other")
    member.activate("finops")
    member.create_draft()
    assert statuses(other)["finops"] == "AVAILABLE"
    assert other.request("GET", "/api/ontology/drafts/open").status_code == 404
    with scoped_tx(other.tenant) as conn:
        assert conn.execute(text("SELECT count(*) FROM ontology_artifacts")).scalar() == 0


@pytest.mark.db
def test_cross_tenant_draft_edit_is_a_404(member):
    other = Member("Other")
    draft = member.create_draft().json()
    assert other.edit(draft, [{"@id": "tenant:X"}]).status_code == 404


@pytest.mark.db
def test_non_member_cannot_activate_a_pack(member):
    outsider = Principal(make_user(), PrincipalKind.HUMAN, email_verified=True)
    member.principal = outsider
    assert member.activate("finops").status_code == 404


@pytest.mark.db
def test_draft_stores_pack_content_that_changed_after_activation(member, monkeypatch):
    member.activate("finops")
    original = artifacts.pack_artifacts(load_domain_packs()["finops"])[0]
    changed = {**original.content, "rdfs:comment": "changed"}
    updated = artifacts.Artifact(
        "PACK", "finops", original.version, changed, content_hash(changed)
    )
    monkeypatch.setattr(store, "pack_artifacts", lambda pack: [updated])
    draft = member.create_draft().json()
    assert draft["artifacts"][1]["content_hash"] == updated.content_hash
    assert draft["artifacts"][1]["content"]["rdfs:comment"] == "changed"
