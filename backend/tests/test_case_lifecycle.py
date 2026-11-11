"""Case triage and the spec 9.3 lifecycle through the routes (task 3.4, AC-CASE-01, AC-CASE-02)."""

from uuid import uuid4

import pytest
from sqlalchemy import text

from app.db import scoped_tx
from tests.domain_rows import seed_domain_rows
from tests.support import grant_set
from tests.test_semantics_store import Member

pytestmark = pytest.mark.db


class Operator(Member):
    def __init__(self) -> None:
        super().__init__()
        grant_set(self.tenant, self.principal.id, "domain_operator")
        self.case = seed_domain_rows(self.tenant, self.principal.id)["domain_cases"]

    def patch(self, **body):
        return self.request("PATCH", f"/api/domain-cases/{self.case}", json=body)

    def reopen(self, **body):
        return self.request("POST", f"/api/domain-cases/{self.case}/reopen", json=body)

    def status(self) -> str:
        with scoped_tx(self.tenant) as conn:
            return conn.execute(
                text("SELECT status FROM domain_cases WHERE id = :id"), {"id": self.case}
            ).scalar_one()

    def audits(self) -> list[dict]:
        with scoped_tx(self.tenant) as conn:
            return [row.detail | {"action": row.action} for row in conn.execute(
                text("SELECT action, detail FROM audit_logs WHERE resource = 'case' "
                     "ORDER BY created_at")
            )]


@pytest.fixture
def operator() -> Operator:
    return Operator()


def ok(response) -> dict:
    assert response.status_code == 200, response.text
    return response.json()


def test_cases_are_listed_with_their_finding(operator):
    (case,) = operator.request("GET", "/api/domain-cases").json()
    assert case["id"] == str(operator.case)
    assert (case["expected_amount"], case["observed_amount"], case["currency"]) == (
        "100.000001", "99.500000", "USD"
    )
    filtered = operator.request("GET", "/api/domain-cases", params={"status": "CLOSED"})
    assert filtered.json() == []


def test_triage_follows_the_main_path(operator):
    assert ok(operator.patch(status="INVESTIGATING"))["status"] == "INVESTIGATING"
    assert ok(operator.patch(status="HUMAN_REVIEW"))["status"] == "HUMAN_REVIEW"
    assert [a["to"] for a in operator.audits()] == ["INVESTIGATING", "HUMAN_REVIEW"]


@pytest.mark.parametrize("target", ["DISMISSED", "CLOSED"])
def test_dismiss_and_close_need_a_reason(operator, target):
    if target == "CLOSED":
        ok(operator.patch(status="DISMISSED", reason="duplicate of another case"))
    response = operator.patch(status=target)
    assert response.status_code == 422


def test_dismiss_close_and_reopen_are_audited_with_reasons(operator):
    ok(operator.patch(status="DISMISSED", reason="fixture noise"))
    ok(operator.patch(status="CLOSED", reason="done"))
    reopened = ok(operator.reopen(reason="the variance came back"))
    assert reopened["status"] == "OPEN"
    trail = operator.audits()
    assert [(a["action"], a["to"], a["reason"]) for a in trail] == [
        ("case.triaged", "DISMISSED", "fixture noise"),
        ("case.triaged", "CLOSED", "done"),
        ("case.reopened", "OPEN", "the variance came back"),
    ]


def test_reopen_applies_only_to_closed_cases(operator):
    response = operator.reopen(reason="why not")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INVALID_CASE_TRANSITION"


def test_reopen_needs_a_reason(operator):
    ok(operator.patch(status="DISMISSED", reason="x"))
    ok(operator.patch(status="CLOSED", reason="x"))
    assert operator.reopen().status_code == 422


@pytest.mark.parametrize("target", ["RESOLVED", "ACTION_PENDING", "OPEN"])
def test_triage_never_sets_execution_states(operator, target):
    assert operator.patch(status=target, reason="x").status_code == 422
    assert operator.status() == "OPEN"


def test_an_invalid_transition_is_refused(operator):
    response = operator.patch(status="CLOSED", reason="skip ahead")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INVALID_CASE_TRANSITION"


@pytest.mark.parametrize("target", ["DISMISSED", "CLOSED", "INVESTIGATING"])
def test_a_case_with_a_side_effect_in_flight_cannot_be_changed(operator, target):
    ok(operator.patch(status="INVESTIGATING"))
    ok(operator.patch(status="HUMAN_REVIEW"))
    with scoped_tx(operator.tenant) as conn:  # the playbook path, not triage, does this
        conn.execute(text("UPDATE domain_cases SET status = 'ACTION_PENDING' WHERE id = :id"),
                     {"id": operator.case})
    response = operator.patch(status=target, reason="stop")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CASE_ACTION_IN_FLIGHT"
    assert operator.status() == "ACTION_PENDING"


def test_assignment_needs_an_active_member(operator):
    assert operator.patch(assignee_id=str(uuid4())).status_code == 422
    assigned = ok(operator.patch(assignee_id=str(operator.principal.id), comment="mine"))
    assert assigned["assignee_id"] == str(operator.principal.id)
    assert assigned["status"] == "OPEN"


@pytest.mark.parametrize("field", ["tenant_id", "approver_id", "execution_id", "case_type"])
def test_triage_body_is_strict(operator, field):
    assert operator.patch(comment="x", **{field: "y"}).status_code == 422


def test_an_empty_triage_is_refused(operator):
    assert operator.patch().status_code == 422


def test_another_tenants_case_is_404(operator):
    other = Operator()
    response = other.request(
        "PATCH", f"/api/domain-cases/{operator.case}", json={"status": "INVESTIGATING"}
    )
    assert response.status_code == 404
    assert operator.status() == "OPEN"
    assert [c["id"] for c in other.request("GET", "/api/domain-cases").json()] == [
        str(other.case)
    ]
