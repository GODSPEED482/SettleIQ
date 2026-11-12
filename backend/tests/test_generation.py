"""Ontology draft generation and the dispatcher queue (task 2.2, AC-ONT-03, AC-ONT-04)."""

import os
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.exc import DBAPIError

from app.config import get_settings
from app.db import global_tx, scoped_tx
from app.investigation import dispatcher
from app.semantics import generation
from app.semantics.generation import (
    DiscoveredObject,
    OntologyProposal,
    ProposedTerm,
    UngroundedProposal,
    ValueKind,
    ground_items,
    ground_proposal,
)
from tests.test_semantics_store import Member

ORDERS = {
    "source_id": "s1",
    "object": "orders",
    "fields": [
        {"name": "id", "datatype": "uuid", "nullable": False, "unique": True},
        {"name": "customer_id", "datatype": "uuid", "nullable": False, "unique": False},
        {"name": "total", "datatype": "numeric", "nullable": False, "unique": False,
         "profile": {"min": "1.00", "max": "900.00"}},
    ],
    "foreign_keys": [{"field": "customer_id", "references": "customers.id"}],
}
METADATA = [DiscoveredObject(**ORDERS)]
KNOWN = ["finops:Order", "finops:Payment"]


def evidence(kind: str = "field_profile", field: str = "total") -> dict:
    return {"kind": kind, "field": field, "detail": "range looks monetary"}


def term(**overrides) -> dict:
    return {
        "id": "tenant:VipOrder", "kind": "class", "label": "VIP order",
        "subclass_of": "finops:Order", "domain": None, "range": None,
        "evidence": [evidence()],
    } | overrides


def binding(**overrides) -> dict:
    return {
        "source_id": "s1", "object": "orders", "field": "total", "property": "finops:Order",
        "class_iri": "finops:Order", "value_kind": "text", "currency_field": None,
        "confidence": "medium", "evidence": [evidence()],
    } | overrides


def proposal(terms=(), bindings=()) -> OntologyProposal:
    return OntologyProposal(terms=list(terms), bindings=list(bindings))


def ground(terms=(), bindings=()) -> list[dict]:
    return ground_proposal(proposal(terms, bindings), METADATA, KNOWN)


# Grounding, no database


def test_binding_is_always_proposed_and_inferred():
    item = ground(bindings=[binding()])[0]
    assert (item["status"], item["basis"]) == ("PROPOSED", "inferred")


def test_foreign_key_is_recorded_as_observed_structural_evidence_only():
    item = ground(terms=[term(evidence=[evidence("foreign_key", "customer_id")])])[0]
    assert item["evidence"][0]["kind"] == "foreign_key"
    assert item["evidence"][0]["basis"] == "observed"
    assert (item["status"], item["basis"]) == ("PROPOSED", "inferred")


def test_name_similarity_is_labelled_inferred():
    item = ground(bindings=[binding(evidence=[evidence("name_similarity")])])[0]
    assert item["evidence"][0]["basis"] == "inferred"


@pytest.mark.parametrize(
    ("kind", "field"), [("foreign_key", "customer_id"), ("field_profile", "total")]
)
def test_model_text_is_never_labelled_observed(kind, field):
    item = ground(bindings=[binding(evidence=[evidence(kind, field)])])[0]["evidence"][0]
    assert item["basis"] == "observed"
    assert "looks monetary" not in item["detail"]
    assert field in item["detail"]
    assert item["rationale"] == "range looks monetary"


def test_claimed_foreign_key_must_exist():
    with pytest.raises(UngroundedProposal, match="no foreign key"):
        ground(bindings=[binding(evidence=[evidence("foreign_key", "total")])])


def test_binding_to_an_invented_field_is_refused():
    with pytest.raises(UngroundedProposal, match="unknown field"):
        ground(bindings=[binding(field="invented")])


def test_binding_to_an_invented_object_is_refused():
    with pytest.raises(UngroundedProposal, match="unknown field"):
        ground(bindings=[binding(object="invented")])


def test_binding_to_an_invented_property_is_refused():
    with pytest.raises(UngroundedProposal, match="unknown property"):
        ground(bindings=[binding(property="finops:Invented")])


def test_binding_may_target_a_term_proposed_alongside_it():
    assert len(ground(terms=[term()], bindings=[binding(property="tenant:VipOrder")])) == 2


def test_term_cannot_reference_an_invented_parent():
    with pytest.raises(UngroundedProposal, match="unknown term"):
        ground(terms=[term(subclass_of="finops:Invented")])


def test_evidence_must_cite_a_real_field():
    with pytest.raises(UngroundedProposal, match="unknown field"):
        ground(bindings=[binding(evidence=[evidence("field_profile", "invented")])])


def test_evidence_may_name_a_field_with_its_object():
    item = ground(bindings=[binding(evidence=[evidence("field_profile", "orders.total")])])[0]
    assert item["evidence"][0]["basis"] == "observed"


def test_a_qualified_field_must_belong_to_the_object_it_names():
    with pytest.raises(UngroundedProposal, match="unknown field"):
        ground(bindings=[binding(evidence=[evidence("field_profile", "customers.total")])])


def test_a_qualified_foreign_key_is_still_checked():
    ground(terms=[term(evidence=[evidence("foreign_key", "orders.customer_id")])])
    with pytest.raises(UngroundedProposal, match="no foreign key"):
        ground(terms=[term(evidence=[evidence("foreign_key", "orders.total")])])


def test_a_binding_field_named_with_its_object_is_stored_bare():
    assert ground(bindings=[binding(field="orders.total")])[0]["field"] == "total"


def test_a_wrong_source_id_resolves_only_to_a_uniquely_named_object():
    item = ground(bindings=[binding(source_id="invented")])[0]
    assert item["source_id"] == "s1"
    twice = [*METADATA, DiscoveredObject(**(ORDERS | {"source_id": "s2"}))]
    with pytest.raises(UngroundedProposal, match="unknown field"):
        ground_proposal(proposal(bindings=[binding(source_id="invented")]), twice, KNOWN)


def test_one_ungrounded_item_does_not_discard_the_grounded_ones():
    grounded, refused = ground_items(
        proposal(
            terms=[term(), term(id="tenant:Ghost", subclass_of="finops:Invented")],
            bindings=[binding(), binding(field="invented")],
        ),
        METADATA,
        KNOWN,
    )
    assert [(item["type"], item.get("id", item.get("field"))) for item in grounded] == [
        ("term", "tenant:VipOrder"),
        ("binding", "total"),
    ]
    assert refused == [
        ("tenant:Ghost", "unknown term finops:Invented"),
        ("orders.invented", "unknown field orders.invented"),
    ]


def test_items_leaning_on_a_refused_term_are_refused_with_it():
    grounded, refused = ground_items(
        proposal(
            terms=[
                term(id="tenant:Ghost", subclass_of="finops:Invented"),
                term(id="tenant:GhostChild", subclass_of="tenant:Ghost"),
            ],
            bindings=[binding(property="tenant:GhostChild")],
        ),
        METADATA,
        KNOWN,
    )
    assert grounded == []
    assert [item for item, _ in refused] == ["tenant:Ghost", "tenant:GhostChild", "orders.total"]


@pytest.mark.parametrize("bad_id", ["finops:Order", "core:Tenant", "tenant:", "VipOrder"])
def test_model_cannot_redefine_inherited_terms(bad_id):
    with pytest.raises(ValidationError):
        proposal(terms=[term(id=bad_id)])


@pytest.mark.parametrize("field", ["status", "request_id", "publish"])
def test_model_cannot_set_review_or_workflow_fields(field):
    with pytest.raises(ValidationError):
        proposal(bindings=[binding(**{field: "PUBLISHED"})])


def test_proposal_needs_evidence():
    with pytest.raises(ValidationError):
        proposal(bindings=[binding(evidence=[])])


def test_a_grounded_binding_carries_a_complete_mapping():
    item = ground(bindings=[binding(field="id", value_kind="identifier")])[0]
    assert item["mapping"] == {
        "class_iri": "finops:Order", "datatype": "string", "transform": {"name": "identity"},
        "value_basis": "identifier", "temporal_context": "any", "currency_field": None,
        "unit": None, "nullable": False, "is_unique": True, "sync_strategy": "batch",
    }


def test_status_is_compared_in_upper_case_as_a_current_value():
    mapping = ground(bindings=[binding(value_kind="status")])[0]["mapping"]
    assert (mapping["transform"], mapping["value_basis"], mapping["temporal_context"]) == (
        {"name": "upper"}, "status", "current")


def test_binding_to_an_invented_class_is_refused():
    with pytest.raises(UngroundedProposal, match="unknown class"):
        ground(bindings=[binding(class_iri="finops:Invented")])


def test_money_needs_a_currency_field_on_the_same_object():
    with pytest.raises(UngroundedProposal, match="no currency field"):
        ground(bindings=[binding(value_kind="money", currency_field=None)])
    with pytest.raises(UngroundedProposal, match="no currency field"):
        ground(bindings=[binding(value_kind="money", currency_field="customers.currency")])
    priced = DiscoveredObject(**ORDERS | {"fields": [*ORDERS["fields"], {
        "name": "currency", "datatype": "text", "nullable": False, "unique": False}]})
    item = ground_proposal(
        proposal(bindings=[binding(value_kind="money", currency_field="orders.currency")]),
        [priced], KNOWN,
    )[0]
    assert item["currency_field"] == item["mapping"]["currency_field"] == "currency"
    assert (item["mapping"]["datatype"], item["mapping"]["transform"]) == (
        "decimal", {"name": "decimal_string"})


def test_only_money_keeps_a_currency_field():
    item = ground(bindings=[binding(value_kind="text", currency_field="total")])[0]
    assert item["mapping"]["currency_field"] is None


def test_the_agent_offers_the_same_value_kinds():
    agent = (Path(__file__).parents[2] / "agent" / "settleiq_agent" / "drafting.py").read_text(
        encoding="utf-8")
    kinds = ", ".join(f'"{kind}"' for kind in ValueKind.__args__)
    assert f"value_kind: Literal[{kinds}]" in agent


# Queue, database


class DispatcherRole:
    """Runs dispatcher code under its real database role, leaving API calls on the runtime role."""

    def __init__(self, engine) -> None:
        self.engine = engine

    def role(self):
        return patch("app.db.get_engine", lambda: self.engine)

    def claim(self):
        with self.role():
            return dispatcher.claim_next_job()

    def run(self, job) -> None:
        with self.role():
            dispatcher.run_job(job)

    def expire(self) -> int:
        with self.role():
            return dispatcher.expire_stale_leases()


@pytest.fixture
def as_dispatcher():
    url = make_url(get_settings().database_url).set(
        username="settleiq_dispatcher", password=os.environ["DISPATCHER_DB_PASSWORD"]
    )
    role = DispatcherRole(create_engine(url))
    # Other tests leave jobs behind; a stale RUNNING row would hold the concurrency cap.
    with role.role(), global_tx() as conn:
        conn.execute(
            text(
                "UPDATE agent_jobs SET status = 'FAILED', error = 'TEST_RESET', "
                "finished_at = now() WHERE status IN ('QUEUED', 'RUNNING')"
            )
        )
    yield role
    role.engine.dispose()


@pytest.fixture
def discovered(monkeypatch):
    monkeypatch.setattr(generation, "load_discovered_metadata", lambda conn: METADATA)


@pytest.fixture
def member_with_draft(discovered) -> Member:
    member = Member()
    member.activate("finops")
    assert member.create_draft().status_code == 201
    return member


def generate(member: Member):
    return member.request("POST", "/api/ontology/generate")


def job_status(member: Member, job_id: str) -> dict:
    return member.request("GET", f"/api/ontology/generation-jobs/{job_id}").json()


def open_draft(member: Member) -> dict:
    return member.request("GET", "/api/ontology/drafts/open").json()


def stored_proposals(member: Member) -> list:
    with scoped_tx(member.tenant) as conn:
        return conn.execute(
            text("SELECT proposals FROM ontology_drafts WHERE status = 'OPEN'")
        ).scalar_one()


def with_agent(monkeypatch, output) -> None:
    monkeypatch.setattr(dispatcher, "_call_agent", lambda path, body: output)


GOOD_OUTPUT = {"terms": [term()], "bindings": [binding()]}


@pytest.mark.db
def test_generation_needs_an_open_draft(discovered):
    member = Member()
    response = generate(member)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "NO_OPEN_DRAFT"


@pytest.mark.db
def test_generation_refuses_a_draft_started_before_a_pack_was_activated(member_with_draft):
    assert member_with_draft.activate("saas").status_code == 200
    response = generate(member_with_draft)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DRAFT_MISSING_PACKS"
    assert "saas" in response.json()["error"]["message"]
    with scoped_tx(member_with_draft.tenant) as conn:
        assert conn.execute(text("SELECT count(*) FROM agent_jobs")).scalar() == 0
    draft_id = open_draft(member_with_draft)["id"]
    member_with_draft.request("POST", f"/api/ontology/drafts/{draft_id}/abandon")
    assert member_with_draft.create_draft().status_code == 201
    assert generate(member_with_draft).status_code == 202


@pytest.mark.db
def test_generation_refuses_without_discovered_metadata():
    member = Member()
    member.create_draft()
    response = generate(member)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "NO_DISCOVERED_METADATA"


@pytest.mark.db
def test_generate_queues_a_job_and_returns_202(member_with_draft):
    response = generate(member_with_draft)
    assert response.status_code == 202
    assert job_status(member_with_draft, response.json()["job_id"])["status"] == "QUEUED"


@pytest.mark.db
def test_payload_carries_metadata_and_known_terms_only(member_with_draft):
    job_id = generate(member_with_draft).json()["job_id"]
    with scoped_tx(member_with_draft.tenant) as conn:
        payload = conn.execute(
            text("SELECT payload FROM agent_jobs WHERE id = :i"), {"i": job_id}
        ).scalar_one()
    assert set(payload) == {"metadata", "known_terms", "term_details", "resolution"}
    assert "finops:Order" in payload["known_terms"]
    assert "core:Tenant" in payload["known_terms"]
    details = {term["id"]: term for term in payload["term_details"]}
    assert set(details) == set(payload["known_terms"])
    assert details["finops:Order"]["kind"] == "class"
    assert details["finops:forOrder"]["range"] == "finops:Order"
    assert details["finops:Capture"]["subclass_of"] == ["finops:FinancialEntry"]
    assert payload["resolution"]["identities"]["finops:Capture"] == ["finops:providerReference"]
    assert {"subject": "finops:Capture", "predicate": "finops:forOrder",
            "object": "finops:Order", "via": "finops:orderReference"} in (
        payload["resolution"]["relationships"])


@pytest.mark.db
def test_second_generation_while_one_is_queued_is_refused(member_with_draft):
    assert generate(member_with_draft).status_code == 202
    response = generate(member_with_draft)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "GENERATION_IN_PROGRESS"


@pytest.mark.db
def test_successful_job_stores_proposals_and_changes_no_draft_content(
    member_with_draft, as_dispatcher, monkeypatch
):
    before = open_draft(member_with_draft)
    job_id = generate(member_with_draft).json()["job_id"]
    with_agent(monkeypatch, GOOD_OUTPUT)
    job = as_dispatcher.claim()
    assert str(job.id) == job_id
    as_dispatcher.run(job)
    assert job_status(member_with_draft, job_id)["status"] == "SUCCEEDED"
    after = open_draft(member_with_draft)
    assert after["revision"] == before["revision"]
    assert after["tenant_extension"] == before["tenant_extension"]
    assert [p["status"] for p in stored_proposals(member_with_draft)] == ["PROPOSED", "PROPOSED"]


@pytest.mark.db
def test_draft_reads_return_proposals_with_their_basis(
    member_with_draft, as_dispatcher, monkeypatch
):
    assert open_draft(member_with_draft)["proposals"] == []
    generate(member_with_draft)
    with_agent(monkeypatch, GOOD_OUTPUT)
    as_dispatcher.run(as_dispatcher.claim())
    proposals = open_draft(member_with_draft)["proposals"]
    assert [(p["type"], p["status"], p["basis"]) for p in proposals] == [
        ("term", "PROPOSED", "inferred"),
        ("binding", "PROPOSED", "inferred"),
    ]
    assert {e["basis"] for p in proposals for e in p["evidence"]} == {"observed"}


@pytest.mark.db
def test_proposals_are_outside_the_candidate_hash(member_with_draft, as_dispatcher, monkeypatch):
    def candidate_hash() -> str:
        draft_id = open_draft(member_with_draft)["id"]
        response = member_with_draft.request(
            "POST", "/api/ontology/validate", json={"draft_id": draft_id}
        )
        return response.json()["content_hash"]

    before = candidate_hash()
    generate(member_with_draft)
    with_agent(monkeypatch, GOOD_OUTPUT)
    as_dispatcher.run(as_dispatcher.claim())
    assert candidate_hash() == before


@pytest.mark.db
def test_generation_never_publishes(member_with_draft, as_dispatcher, monkeypatch):
    generate(member_with_draft)
    with_agent(monkeypatch, GOOD_OUTPUT)
    as_dispatcher.run(as_dispatcher.claim())
    with scoped_tx(member_with_draft.tenant) as conn:
        assert conn.execute(text("SELECT count(*) FROM ontologies")).scalar() == 0


@pytest.mark.db
def test_completion_is_audited_as_the_agent_for_the_invoker(
    member_with_draft, as_dispatcher, monkeypatch
):
    generate(member_with_draft)
    with_agent(monkeypatch, GOOD_OUTPUT)
    as_dispatcher.run(as_dispatcher.claim())
    with scoped_tx(member_with_draft.tenant) as conn:
        row = conn.execute(
            text(
                "SELECT principal_kind, invoker_user_id FROM audit_logs "
                "WHERE action = 'ontology.generation.completed'"
            )
        ).one()
    assert tuple(row) == ("agent", member_with_draft.principal.id)


@pytest.mark.db
@pytest.mark.parametrize(
    ("output", "reason"),
    [
        ({"terms": "nope"}, "INVALID_AGENT_OUTPUT"),
        ({"terms": [], "bindings": [], "publish": True}, "INVALID_AGENT_OUTPUT"),
        ({"terms": [], "bindings": [binding(field="invented")]}, "UNGROUNDED_AGENT_OUTPUT"),
    ],
)
def test_bad_agent_output_fails_the_job_and_writes_nothing(
    member_with_draft, as_dispatcher, monkeypatch, output, reason
):
    job_id = generate(member_with_draft).json()["job_id"]
    with_agent(monkeypatch, output)
    as_dispatcher.run(as_dispatcher.claim())
    status = job_status(member_with_draft, job_id)
    assert (status["status"], status["error"]) == ("FAILED", reason)
    assert stored_proposals(member_with_draft) == []


@pytest.mark.db
def test_a_partly_grounded_answer_keeps_only_its_grounded_items(
    member_with_draft, as_dispatcher, monkeypatch
):
    job_id = generate(member_with_draft).json()["job_id"]
    with_agent(monkeypatch, {
        "terms": [term(), term(id="tenant:Ghost", subclass_of="finops:Invented")],
        "bindings": [binding(), binding(field="invented")],
    })
    as_dispatcher.run(as_dispatcher.claim())
    assert job_status(member_with_draft, job_id)["status"] == "SUCCEEDED"
    proposals = stored_proposals(member_with_draft)
    assert [p.get("id", p.get("field")) for p in proposals] == ["tenant:VipOrder", "total"]
    with scoped_tx(member_with_draft.tenant) as conn:
        details = conn.execute(
            text(
                "SELECT detail FROM audit_logs WHERE action = 'ontology.generation.completed'"
            )
        ).scalar_one()
    assert (details["proposals"], details["refused_as_ungrounded"]) == (2, 2)


@pytest.mark.db
def test_agent_being_down_fails_the_job(member_with_draft, as_dispatcher, monkeypatch):
    job_id = generate(member_with_draft).json()["job_id"]

    def down(path, body):
        raise dispatcher.JobFailed("AGENT_UNAVAILABLE")

    monkeypatch.setattr(dispatcher, "_call_agent", down)
    as_dispatcher.run(as_dispatcher.claim())
    assert job_status(member_with_draft, job_id)["error"] == "AGENT_UNAVAILABLE"


@pytest.mark.db
def test_a_job_can_be_retried_after_it_fails(member_with_draft, as_dispatcher, monkeypatch):
    generate(member_with_draft)
    with_agent(monkeypatch, {"bad": True})
    as_dispatcher.run(as_dispatcher.claim())
    assert generate(member_with_draft).status_code == 202


@pytest.mark.db
def test_expired_lease_fails_the_job_and_late_results_are_dropped(
    member_with_draft, as_dispatcher, monkeypatch
):
    job_id = generate(member_with_draft).json()["job_id"]
    job = as_dispatcher.claim()
    with as_dispatcher.role(), global_tx() as conn:
        conn.execute(
            text(
                "UPDATE agent_jobs SET lease_expires_at = now() - interval '1 minute' "
                "WHERE id = :i"
            ),
            {"i": job_id},
        )
    assert as_dispatcher.expire() == 1
    with_agent(monkeypatch, GOOD_OUTPUT)
    as_dispatcher.run(job)
    status = job_status(member_with_draft, job_id)
    assert (status["status"], status["error"]) == ("FAILED", "LEASE_EXPIRED")
    assert stored_proposals(member_with_draft) == []


@pytest.mark.db
def test_concurrency_cap_holds_back_the_next_job(discovered, as_dispatcher):
    first, second = Member(), Member()
    for member in (first, second):
        member.create_draft()
        generate(member)
    assert as_dispatcher.claim() is not None
    assert as_dispatcher.claim() is None


@pytest.mark.db
def test_tenants_cannot_see_each_others_jobs(member_with_draft):
    job_id = generate(member_with_draft).json()["job_id"]
    other = Member("Other")
    assert other.request("GET", f"/api/ontology/generation-jobs/{job_id}").status_code == 404


@pytest.mark.db
def test_runtime_role_sees_no_job_without_tenant_context(member_with_draft):
    generate(member_with_draft)
    with global_tx() as conn:
        assert conn.execute(text("SELECT count(*) FROM agent_jobs")).scalar() == 0


@pytest.mark.db
def test_dispatcher_role_is_not_privileged(as_dispatcher):
    with as_dispatcher.role(), global_tx() as conn:
        row = conn.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        ).one()
    assert tuple(row) == (False, False)


@pytest.mark.db
def test_dispatcher_cross_tenant_access_is_limited_to_jobs(member_with_draft, as_dispatcher):
    with as_dispatcher.role(), global_tx() as conn:
        assert conn.execute(text("SELECT count(*) FROM ontology_drafts")).scalar() == 0
        assert conn.execute(text("SELECT count(*) FROM service_principals")).scalar() == 0


@pytest.mark.db
@pytest.mark.parametrize(
    "statement",
    [
        "SELECT * FROM user_credentials",
        "SELECT * FROM auth_sessions",
        "SELECT * FROM member_grants",
        "SELECT * FROM audit_logs",
        "UPDATE ontology_drafts SET status = 'PUBLISHED'",
        "INSERT INTO ontologies (id) VALUES (gen_random_uuid())",
        "DELETE FROM agent_jobs",
    ],
)
def test_dispatcher_role_has_no_other_table_access(as_dispatcher, statement):
    with as_dispatcher.role(), pytest.raises(DBAPIError, match="permission denied"):
        with scoped_tx(uuid4()) as conn:
            conn.execute(text(statement))


def test_the_agent_generates_under_the_same_term_id_rule():
    """The agent repeats the term ID pattern in its structured-output schema, so the model is
    held to it while generating; the agent cannot import this module, so compare the text."""
    agent = (Path(__file__).parents[2] / "agent" / "settleiq_agent" / "drafting.py").read_text(
        encoding="utf-8")
    pattern = ProposedTerm.model_fields["id"].metadata[0].pattern
    assert f'TERM_ID = r"{pattern}"' in agent


# Aligning lookalike new terms to the pack's own terms, no database

SUBSCRIPTIONS = DiscoveredObject(
    source_id="s1", object="subscriptions",
    fields=[{"name": name, "datatype": "text", "nullable": False, "unique": name == "id"}
            for name in ("id", "status", "current_period_end", "period_end", "subscription_plan",
                         "renews_at", "lifecycle_status")],
)
DETAILS = [
    {"id": "core:DomainEntity", "kind": "class", "label": "Domain entity", "subclass_of": []},
    {"id": "saas:Subscription", "kind": "class", "label": "Subscription",
     "subclass_of": ["core:DomainEntity"]},
    {"id": "saas:Invoice", "kind": "class", "label": "Invoice",
     "subclass_of": ["core:DomainEntity"]},
    {"id": "saas:status", "kind": "property", "label": "status", "domain": "core:DomainEntity"},
    {"id": "saas:currentPeriodEnd", "kind": "property", "label": "current period end",
     "domain": "saas:Subscription"},
    {"id": "saas:periodEnd", "kind": "property", "label": "period end", "domain": "saas:Invoice"},
    {"id": "saas:plan", "kind": "property", "label": "plan", "domain": None},
    {"id": "finops:lifecycleStatus", "kind": "property", "label": "lifecycle status",
     "domain": None},
]


def sub_binding(field: str, prop: str, **overrides) -> dict:
    return binding(object="subscriptions", field=field, property=prop,
                   class_iri="saas:Subscription", evidence=[evidence(field=field)]) | overrides


def sub_term(term_id: str, label: str) -> dict:
    return term(id=term_id, kind="property", label=label, subclass_of=None,
                domain="saas:Subscription", evidence=[evidence(field="status")])


def aligned(terms=(), bindings=()) -> list[dict]:
    grounded, refused = ground_items(
        proposal(terms, bindings), [SUBSCRIPTIONS], [d["id"] for d in DETAILS], DETAILS)
    assert refused == []
    return grounded


def test_a_lookalike_new_term_is_replaced_by_the_packs_term_of_the_same_name():
    items = aligned(
        terms=[sub_term("tenant:subscriptionStatus", "Subscription status"),
               sub_term("tenant:subscriptionCurrentPeriodEnd", "Current period end")],
        bindings=[sub_binding("status", "tenant:subscriptionStatus"),
                  sub_binding("current_period_end", "tenant:subscriptionCurrentPeriodEnd")],
    )
    assert [(i["field"], i["property"]) for i in items] == [
        ("status", "saas:status"), ("current_period_end", "saas:currentPeriodEnd")]
    note = items[0]["evidence"][-1]
    assert note["basis"] == "inferred" and "tenant:subscriptionStatus" in note["detail"]


def test_the_field_may_carry_its_class_name_and_the_new_terms_label_may_match():
    items = aligned(
        terms=[sub_term("tenant:renewsAt", "Current period end")],
        bindings=[sub_binding("subscription_plan", "tenant:subscriptionPlan"),
                  sub_binding("renews_at", "tenant:renewsAt")],
    )
    assert [i["property"] for i in items if i["type"] == "binding"] == [
        "saas:plan", "saas:currentPeriodEnd"]


def test_no_term_of_another_pack_or_for_another_class_is_used():
    items = aligned(
        terms=[sub_term("tenant:periodEnd", "Period end"),
               sub_term("tenant:lifecycleStatus", "Lifecycle status")],
        bindings=[sub_binding("period_end", "tenant:periodEnd"),
                  sub_binding("lifecycle_status", "tenant:lifecycleStatus")],
    )
    assert [i["property"] for i in items if i["type"] == "binding"] == [
        "tenant:periodEnd", "tenant:lifecycleStatus"]
    assert {i["id"] for i in items if i["type"] == "term"} == {
        "tenant:periodEnd", "tenant:lifecycleStatus"}


def test_a_new_term_still_bound_elsewhere_is_kept():
    items = aligned(
        terms=[sub_term("tenant:subscriptionStatus", "Subscription status")],
        bindings=[sub_binding("status", "tenant:subscriptionStatus"),
                  sub_binding("lifecycle_status", "tenant:subscriptionStatus")],
    )
    assert "tenant:subscriptionStatus" in {i["id"] for i in items if i["type"] == "term"}
    assert [i["property"] for i in items if i["type"] == "binding"] == [
        "saas:status", "tenant:subscriptionStatus"]


def test_without_term_details_nothing_is_aligned():
    grounded, _ = ground_items(
        proposal([sub_term("tenant:subscriptionStatus", "Status")],
                 [sub_binding("status", "tenant:subscriptionStatus")]),
        [SUBSCRIPTIONS], [d["id"] for d in DETAILS])
    assert grounded[-1]["property"] == "tenant:subscriptionStatus"
