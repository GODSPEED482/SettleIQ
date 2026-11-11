"""Investigations end to end (task 5.7; AC-INV-01, AC-AI-01/03/04/05, POL-08).

A real PB-001 case is investigated through the public start route, the dispatcher (under its own
database role), the delegated token and the private tool API. The agent itself is replaced by a
function that calls the real tools with the token it was given, as the agent service does, so
every check below is the backend's, not a fake's.
"""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

import app.db
from app.agent_tools_api import TOOL_PREFIX
from app.agent_tools_api import app as tools_app
from app.authz.access import Principal, PrincipalKind
from app.authz.guard import TENANT_HEADER
from app.config import get_settings
from app.db import global_tx, scoped_tx
from app.investigation import dispatcher
from app.investigation.tokens import DelegatedToken, mint_token
from tests.domain_fixture import EVIDENCE_ROOT
from tests.support import add_member, grant_set, make_user
from tests.test_generation import as_dispatcher  # noqa: F401 (fixture)
from tests.test_paypal_mappings import PayPalTenant, paypal_capture, pending
from tests.test_semantics_store import Member
from tests.test_semantics_store import app as public_app

pytestmark = pytest.mark.db

RUNTIME_ENGINE = app.db.get_engine
tools = TestClient(tools_app)


@pytest.fixture(autouse=True)
def token_secret(monkeypatch):
    monkeypatch.setattr(get_settings(), "agent_token_secret", "s" * 48)


@pytest.fixture(scope="module")
def paypal() -> PayPalTenant:
    return PayPalTenant()


def new_case(tenant: PayPalTenant) -> UUID:
    """A fresh STATUS_MISMATCH case for which PB-001 is eligible."""
    capture, order = f"CAP-{uuid4().hex[:8]}", f"ORD-{uuid4().hex[:8]}"
    tenant.event("PAYMENT.CAPTURE.COMPLETED", paypal_capture(capture, order))
    tenant.merchant_payment(pending(capture, order))
    tenant.run()
    with scoped_tx(tenant.tenant) as conn:
        return conn.execute(
            text(
                """
                SELECT c.id FROM domain_cases c
                WHERE c.case_type = 'STATUS_MISMATCH' AND EXISTS (
                    SELECT 1 FROM property_observations o
                    WHERE o.entity_id = ANY (c.entity_ids)
                      AND o.property_iri = 'finops:providerReference'
                      AND o.normalized_value = :capture)
                ORDER BY c.created_at DESC LIMIT 1
                """
            ),
            {"capture": capture},
        ).scalar_one()


def start(member: Member, case_id: UUID):
    return member.request("POST", f"/api/agent/investigate/{case_id}")


def investigation(tenant: UUID, investigation_id: str) -> Any:
    with scoped_tx(tenant) as conn:
        return conn.execute(
            text("SELECT * FROM investigations WHERE id = :id"), {"id": investigation_id}
        ).one()


def token_for(tenant: UUID, run_id: str, **changes) -> str:
    row = investigation(tenant, run_id)
    values = {"investigation_id": row.id, "tenant_id": tenant, "case_id": row.case_id,
              "invoker_id": row.invoked_by,
              "expires_at": datetime.now(UTC) + timedelta(minutes=5)}
    return mint_token(DelegatedToken(**(values | changes)))


def call(token: str | None, tool: str, args: dict | None = None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return tools.post(f"{TOOL_PREFIX}/{tool}", json=args or {}, headers=headers)


def trace(tenant: UUID, investigation_id: str) -> list[Any]:
    with scoped_tx(tenant) as conn:
        return conn.execute(
            text(
                "SELECT * FROM investigation_tool_calls WHERE investigation_id = :id "
                "ORDER BY created_at, id"
            ),
            {"id": investigation_id},
        ).all()


@pytest.fixture
def running(paypal) -> tuple[UUID, str, str]:
    case_id = new_case(paypal)
    response = start(paypal, case_id)
    assert response.status_code == 202, response.text
    investigation_id = response.json()["investigation_id"]
    return case_id, investigation_id, token_for(paypal.tenant, investigation_id)


# Starting (AC-INV-01)


def test_start_writes_a_running_investigation_and_its_job(paypal, running):
    case_id, investigation_id, _ = running
    row = investigation(paypal.tenant, investigation_id)
    assert (row.status, row.case_id, row.invoked_by) == ("RUNNING", case_id, paypal.principal.id)
    assert row.pack_namespace == "finops"
    assert len(row.tool_registry_version) == 64
    assert row.input_evidence["case_type"] == "STATUS_MISMATCH"
    with scoped_tx(paypal.tenant) as conn:
        job = conn.execute(text("SELECT * FROM agent_jobs WHERE id = :id"),
                           {"id": row.job_id}).one()
    assert (job.kind, job.subject_id, job.status) == ("INVESTIGATION", row.id, "QUEUED")


def test_a_cases_investigations_are_listed_newest_first(paypal, running):
    case_id, investigation_id, _ = running
    listed = paypal.request("GET", "/api/investigations", params={"case_id": str(case_id)})
    assert listed.status_code == 200, listed.text
    [summary] = listed.json()
    assert (summary["investigation_id"], summary["status"]) == (investigation_id, "RUNNING")
    other = paypal.request("GET", "/api/investigations", params={"case_id": str(uuid4())})
    assert other.json() == []


def test_one_running_investigation_per_case(paypal, running):
    case_id, _, _ = running
    response = start(paypal, case_id)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INVESTIGATION_RUNNING"


def test_another_tenants_case_cannot_be_investigated(paypal):
    case_id = new_case(paypal)
    other = Member()
    assert other.activate("finops").status_code == 200
    grant_set(other.tenant, other.principal.id, "domain_operator")
    assert start(other, case_id).status_code == 404


# The delegated token (POL-08)


def test_a_tool_call_needs_the_token(running):
    assert call(None, "get_case_evidence").status_code == 401
    assert call("v1.forged.signature", "get_case_evidence").status_code == 401


@pytest.mark.parametrize("claim", ["case_id", "invoker_id", "investigation_id"])
def test_a_token_for_another_case_invoker_or_run_is_refused(paypal, running, claim):
    _, investigation_id, _ = running
    token = token_for(paypal.tenant, investigation_id, **{claim: uuid4()})
    assert call(token, "get_case_evidence").status_code == 401


def test_a_token_for_another_tenant_is_refused(paypal, running):
    _, investigation_id, _ = running
    token = token_for(paypal.tenant, investigation_id, tenant_id=Member().tenant)
    assert call(token, "get_case_evidence").status_code == 401


def test_an_expired_token_is_refused(paypal, running):
    _, investigation_id, _ = running
    token = token_for(paypal.tenant, investigation_id,
                      expires_at=datetime.now(UTC) - timedelta(seconds=1))
    assert call(token, "get_case_evidence").status_code == 401


class Operator(Member):
    """A non-owner member of an existing tenant."""

    def __init__(self, tenant: UUID) -> None:
        self.principal = Principal(make_user(), PrincipalKind.HUMAN, email_verified=True)
        self.tenant = tenant
        self.client = TestClient(public_app, headers={TENANT_HEADER: str(tenant)})


def test_revoking_the_invokers_access_stops_the_next_call(paypal):
    operator = Operator(paypal.tenant)
    add_member(paypal.tenant, operator.principal.id)
    grant_set(paypal.tenant, operator.principal.id, "domain_operator")
    response = start(operator, new_case(paypal))
    assert response.status_code == 202, response.text
    investigation_id = response.json()["investigation_id"]
    token = token_for(paypal.tenant, investigation_id)
    assert call(token, "get_case_evidence").status_code == 200

    with scoped_tx(paypal.tenant) as conn:
        conn.execute(
            text("UPDATE member_grants SET revoked_at = now() WHERE user_id = :u"),
            {"u": operator.principal.id},
        )
    refused = call(token, "get_case_evidence")
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "MISSING_PERMISSION"
    last = trace(paypal.tenant, investigation_id)[-1]
    assert (last.outcome, last.tool_name, last.error) == ("DENIED", "get_case_evidence",
                                                          "MISSING_PERMISSION")


# The tools (AC-AI-01, AC-AI-03, AC-AI-05)


def test_case_evidence_is_returned_and_traced(paypal, running):
    case_id, investigation_id, token = running
    response = call(token, "get_case_evidence")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["result"]["case"]["id"] == str(case_id)
    assert body["result"]["source_record_ids"]
    assert str(case_id) in body["evidence_ids"]
    # Every citation is a triple of IDs the trace records, so a cited item can pass grounding.
    citations = body["result"]["citations"]
    assert citations
    for citation in citations:
        assert {citation["evidence_id"], citation["record_id"], citation["source_id"]} <= set(
            body["evidence_ids"])
    [row] = trace(paypal.tenant, investigation_id)
    assert (row.outcome, row.tool_name, row.permission) == ("OK", "get_case_evidence",
                                                            "case.read")
    assert {str(i) for i in row.evidence_ids} == set(body["evidence_ids"])
    assert row.invoker_id == paypal.principal.id
    assert len(row.input_hash) == 64 and len(row.output_hash) == 64


def test_raw_excerpts_are_stored_in_the_trace_only_as_hashes(paypal, running, monkeypatch):
    monkeypatch.setattr(get_settings(), "raw_evidence_dir", str(EVIDENCE_ROOT))
    _, investigation_id, token = running
    response = call(token, "get_case_evidence", {"include_raw_excerpts": True})
    assert response.status_code == 200, response.text
    excerpts = response.json()["result"]["raw_excerpts"]
    assert excerpts and "CAP-" in excerpts[0]["excerpt"]
    stored = trace(paypal.tenant, investigation_id)[-1].output["raw_excerpts"][0]["excerpt"]
    assert set(stored) == {"sha256"}


def test_entities_outside_the_case_are_refused(paypal, running):
    _, investigation_id, token = running
    other_case = new_case(paypal)
    with scoped_tx(paypal.tenant) as conn:
        outside = conn.execute(
            text("SELECT entity_ids[1] FROM domain_cases WHERE id = :id"), {"id": other_case}
        ).scalar_one()
    for tool in ("get_property_observations", "trace_domain_relationships",
                 "get_entity_resolution_trace"):
        response = call(token, tool, {"entity_id": str(outside)})
        assert response.status_code == 422, tool
    assert {r.outcome for r in trace(paypal.tenant, investigation_id)} == {"ERROR"}


def test_observations_keep_provenance(paypal, running):
    case_id, _, token = running
    with scoped_tx(paypal.tenant) as conn:
        entity = conn.execute(
            text("SELECT entity_ids[1] FROM domain_cases WHERE id = :id"), {"id": case_id}
        ).scalar_one()
    body = call(token, "get_property_observations", {"entity_id": str(entity)}).json()
    observation = body["result"]["observations"][0]
    assert {"source_id", "source_record_id", "connector_id", "value_basis",
            "temporal_context", "observed_at"} <= set(observation)


@pytest.mark.parametrize("args", [{"sql": "SELECT * FROM tenants"},
                                  {"entity_id": "1; DROP TABLE domain_cases"}])
def test_tools_take_typed_arguments_only(running, args):
    _, _, token = running
    tool = "get_case_evidence" if "sql" in args else "get_property_observations"
    assert call(token, tool, args).status_code == 422


def test_a_classification_is_checked_against_the_taxonomy_and_only_proposed(paypal, running):
    case_id, _, token = running
    assert call(token, "classify_domain_case",
                {"classification": "MADE_UP_TYPE"}).status_code == 422
    body = call(token, "classify_domain_case",
                {"classification": "DUPLICATE_CAPTURE"}).json()
    assert body["result"]["status"] == "PROPOSED"
    with scoped_tx(paypal.tenant) as conn:
        assert conn.execute(text("SELECT case_type FROM domain_cases WHERE id = :id"),
                            {"id": case_id}).scalar_one() == "STATUS_MISMATCH"


def test_playbook_tools_are_dry_runs(paypal, running):
    _, _, token = running
    recommended = call(token, "recommend_playbook").json()["result"]["eligible"]
    assert [(p["playbook"], p["requires_approval"]) for p in recommended] == [
        ("finops:PB-001", True)]
    validated = call(token, "validate_playbook", {"playbook": "finops:PB-002"}).json()
    assert validated["result"]["eligible"] is False
    with scoped_tx(paypal.tenant) as conn:
        assert conn.execute(text("SELECT count(*) FROM playbook_requests")).scalar_one() == 0


def test_the_ontology_context_is_the_pinned_composition(running):
    _, _, token = running
    result = call(token, "get_ontology_context").json()["result"]
    assert {t["@id"] for t in result["terms"]} >= {"finops:Capture"}
    assert {layer["namespace"] for layer in result["layers"]} >= {"finops"}


def test_settlement_variance_comes_from_the_pack(running):
    _, _, token = running
    body = call(token, "calculate_settlement_variance").json()
    assert body["result"]["batches"] == []
    assert body["result"]["treatment_version"]


# The dispatcher (AC-AI-02, AC-AI-04, AC-AGENT-02)


@contextmanager
def runtime_role():
    with patch("app.db.get_engine", RUNTIME_ENGINE):
        yield


def agent(answer) -> Any:
    """Stands in for the agent service: real tool calls with the token, then `answer`."""

    def run(path: str, body: dict) -> dict:
        assert path == "/run"
        assert {t["name"] for t in body["tools"]} >= {"get_case_evidence", "recommend_playbook"}
        with runtime_role():
            evidence = call(body["tool_token"], "get_case_evidence").json()["result"]
            recommended = call(body["tool_token"], "recommend_playbook").json()["result"]
        return {"result": answer(body, evidence, recommended), "model": "test-model",
                "prompt_version": "investigation/1"}

    return run


def grounded(body: dict, evidence: dict, recommended: dict) -> dict:
    playbook = recommended["eligible"][0]
    return {
        "case_id": body["case_id"], "classification": "STATUS_MISMATCH",
        "root_cause_summary": "PayPal completed the capture; the merchant still shows pending.",
        "evidence": [{"evidence_id": evidence["case"]["observation_ids"][0],
                      "record_id": evidence["source_record_ids"][0],
                      "source_id": evidence["source_ids"][0],
                      "fact": "PayPal reports the capture COMPLETED."}],
        "recommended_playbook": playbook["playbook"],
        "requires_approval": playbook["requires_approval"],
        "confidence": "high", "missing_evidence": [],
    }


def dispatch(paypal, as_dispatcher, monkeypatch, answer) -> str:  # noqa: F811
    case_id = new_case(paypal)
    investigation_id = start(paypal, case_id).json()["investigation_id"]
    monkeypatch.setattr(dispatcher, "_call_agent", agent(answer))
    job = as_dispatcher.claim()
    assert job.subject_id == UUID(investigation_id)
    as_dispatcher.run(job)
    return investigation_id


def test_a_grounded_answer_is_stored_once(paypal, as_dispatcher, monkeypatch):  # noqa: F811
    investigation_id = dispatch(paypal, as_dispatcher, monkeypatch, grounded)
    row = investigation(paypal.tenant, investigation_id)
    assert row.status == "SUCCEEDED", row.error
    assert (row.model, row.prompt_version) == ("test-model", "investigation/1")
    assert (row.proposed_classification, row.recommended_playbook) == ("STATUS_MISMATCH",
                                                                       "finops:PB-001")
    assert row.result["requires_approval"] is True
    read = paypal.request("GET", f"/api/investigations/{investigation_id}").json()
    assert read["status"] == "SUCCEEDED"
    assert [c["tool"] for c in read["tool_calls"]] == ["get_case_evidence", "recommend_playbook"]
    with scoped_tx(paypal.tenant) as conn:
        audit = conn.execute(
            text("SELECT principal_kind, invoker_user_id FROM audit_logs "
                 "WHERE action = 'investigation.completed' AND detail->>'investigation_id' = :i"),
            {"i": investigation_id},
        ).one()
    assert (audit.principal_kind, audit.invoker_user_id) == ("agent", paypal.principal.id)

    # Written once: the dispatcher cannot change it again, nor rewrite who or what it was.
    with as_dispatcher.role(), pytest.raises(DBAPIError, match="immutable"):
        with scoped_tx(paypal.tenant) as conn:
            conn.execute(text("UPDATE investigations SET status = 'FAILED', error = 'x' "
                              "WHERE id = :id"), {"id": investigation_id})
    with as_dispatcher.role(), pytest.raises(DBAPIError, match="permission denied"):
        with scoped_tx(paypal.tenant) as conn:
            conn.execute(text("UPDATE investigations SET case_id = case_id WHERE id = :id"),
                         {"id": investigation_id})
    # And no tool call joins a finished trace.
    token = token_for(paypal.tenant, investigation_id)
    assert call(token, "get_case_evidence").status_code == 401


def invented_evidence(body, evidence, recommended) -> dict:
    answer = grounded(body, evidence, recommended)
    answer["evidence"][0]["evidence_id"] = str(uuid4())
    return answer


def ineligible_playbook(body, evidence, recommended) -> dict:
    return grounded(body, evidence, recommended) | {"recommended_playbook": "finops:PB-002"}


def wrong_approval(body, evidence, recommended) -> dict:
    return grounded(body, evidence, recommended) | {"requires_approval": False}


def execution_ids(body, evidence, recommended) -> dict:
    return grounded(body, evidence, recommended) | {"execution_id": str(uuid4())}


@pytest.mark.parametrize(("answer", "reason"), [
    (invented_evidence, "REFUSED_UNRETURNED_EVIDENCE"),
    (ineligible_playbook, "REFUSED_INELIGIBLE_PLAYBOOK"),
    (wrong_approval, "REFUSED_WRONG_APPROVAL_REQUIREMENT"),
    (execution_ids, "REFUSED_SCHEMA"),
])
def test_a_refused_answer_is_never_stored(paypal, as_dispatcher, monkeypatch,  # noqa: F811
                                          answer, reason):
    investigation_id = dispatch(paypal, as_dispatcher, monkeypatch, answer)
    row = investigation(paypal.tenant, investigation_id)
    assert (row.status, row.error, row.result) == ("FAILED", reason, None)


def test_an_unreachable_agent_fails_the_investigation(paypal, as_dispatcher,  # noqa: F811
                                                      monkeypatch):
    def unavailable(path, body):
        raise dispatcher.JobFailed("AGENT_UNAVAILABLE")

    investigation_id = start(paypal, new_case(paypal)).json()["investigation_id"]
    monkeypatch.setattr(dispatcher, "_call_agent", unavailable)
    as_dispatcher.run(as_dispatcher.claim())
    row = investigation(paypal.tenant, investigation_id)
    assert (row.status, row.error) == ("FAILED", "AGENT_UNAVAILABLE")


def test_an_expired_lease_fails_the_investigation(paypal, as_dispatcher):  # noqa: F811
    investigation_id = start(paypal, new_case(paypal)).json()["investigation_id"]
    job = as_dispatcher.claim()
    with as_dispatcher.role(), global_tx() as conn:
        conn.execute(text("UPDATE agent_jobs SET lease_expires_at = now() - interval '1 minute' "
                          "WHERE id = :id"), {"id": job.id})
    assert as_dispatcher.expire() == 1
    row = investigation(paypal.tenant, investigation_id)
    assert (row.status, row.error) == ("FAILED", "LEASE_EXPIRED")
