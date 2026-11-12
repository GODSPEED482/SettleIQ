"""The two-stage policy gateway through its routes (task 5.2).

AC-PLY-01, 04, 08, 09; AC-AUTHZ-04 (POL-01), 08 (grant binding), 14 (audit with policy IDs),
15 (SHADOW); AC-EXEC-MODE-04.
"""

import hashlib
from itertools import count
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from app.authz.access import Principal, PrincipalKind
from app.db import scoped_tx
from app.playbooks import gateway
from tests.support import add_member, grant_set, make_user
from tests.test_paypal_mappings import PayPalTenant, paypal_capture, pending
from tests.test_semantics_store import Member

pytestmark = pytest.mark.db

PB001 = "finops:PB-001"
MERCHANT = {"domain_pack_id": "finops", "connector_id": "internal-merchant",
            "action_capability": "merchant.payment_status.correct"}
REFUND = {"domain_pack_id": "finops", "connector_id": "paypal",
          "action_capability": "refunds.create"}
SEQUENCE = count(1)


class Person(Member):
    """Another human in an existing tenant, holding the given permission sets."""

    def __init__(self, owner: Member, *sets: str) -> None:
        user = make_user()
        add_member(owner.tenant, user)
        for key in sets:
            grant_set(owner.tenant, user, key)
        self.principal = Principal(user, PrincipalKind.HUMAN, email_verified=True)
        self.tenant, self.client = owner.tenant, owner.client


@pytest.fixture(scope="module")
def paypal() -> PayPalTenant:
    return PayPalTenant()


@pytest.fixture(scope="module")
def approver(paypal) -> Person:
    return Person(paypal, "approver")


@pytest.fixture(autouse=True)
def granted(monkeypatch) -> list[gateway.ExecutionDispatch]:
    sent: list[gateway.ExecutionDispatch] = []
    monkeypatch.setattr(gateway, "dispatch", sent.append)
    return sent


def status_case(tenant: PayPalTenant) -> UUID:
    """A fresh STATUS_MISMATCH case in HUMAN_REVIEW for which PB-001 is eligible."""
    n = next(SEQUENCE)
    capture, order = f"CAP-GW-{n}-{uuid4().hex[:6]}", f"ORD-GW-{n}"
    tenant.event("PAYMENT.CAPTURE.COMPLETED", paypal_capture(capture, order))
    tenant.merchant_payment(pending(capture, order))
    tenant.run()
    with scoped_tx(tenant.tenant) as conn:
        case_id = conn.execute(
            text(
                """
                SELECT c.id FROM domain_cases c
                JOIN property_observations o ON o.entity_id = ANY (c.entity_ids)
                WHERE c.case_type = 'STATUS_MISMATCH' AND o.normalized_value = :capture
                LIMIT 1
                """
            ),
            {"capture": capture},
        ).scalar_one()
    for status in ("INVESTIGATING", "HUMAN_REVIEW"):
        response = tenant.request("PATCH", f"/api/domain-cases/{case_id}",
                                  json={"status": status})
        assert response.status_code == 200, response.text
    return case_id


def create(member: Member, case_id: UUID, key: str | None = None, playbook: str = PB001,
           **body):
    return member.request(
        "POST", f"/api/playbooks/{playbook}/requests",
        json={"case_id": str(case_id), **body},
        headers={"Idempotency-Key": key or f"key-{uuid4()}"},
    )


def approve(member: Member, request_id: str):
    return member.request("POST", f"/api/playbook-requests/{request_id}/approve")


def put_mode(owner: Member, scope: dict, mode: str):
    return owner.request("PUT", f"/api/tenants/{owner.tenant}/execution-mode",
                         json={**scope, "mode": mode})


def case_status(tenant: Member, case_id: UUID) -> str:
    with scoped_tx(tenant.tenant) as conn:
        return conn.execute(text("SELECT status FROM domain_cases WHERE id = :id"),
                            {"id": case_id}).scalar_one()


def count_rows(tenant: Member, table: str, request_id: str) -> int:
    with scoped_tx(tenant.tenant) as conn:
        return conn.execute(text(f"SELECT count(*) FROM {table} WHERE request_id = :r"),
                            {"r": request_id}).scalar_one()


@pytest.fixture
def shadow(paypal):
    """Every test starts with both scopes back in SHADOW."""
    for scope in (MERCHANT, REFUND):
        assert put_mode(paypal, scope, "SHADOW").status_code == 200
    return paypal


# The request stage (AC-PLY-09, AC-PLY-08, demo step 8)


def test_a_request_and_its_decision_exist_before_any_approval(shadow):
    case_id = status_case(shadow)
    response = create(shadow, case_id)
    assert response.status_code == 201, response.text
    request = response.json()
    assert request["status"] == "NEEDS_APPROVAL"
    assert request["requested_by"] == str(shadow.principal.id)
    [decision] = request["decisions"]
    assert (decision["stage"], decision["decision"], decision["approval_required"]) == (
        "REQUEST", "NEEDS_APPROVAL", True)
    assert request["approval"] is None and request["execution"] is None
    assert request["target"]["payment_id"]
    assert case_status(shadow, case_id) == "ACTION_PENDING"


def test_a_repeated_key_returns_the_prior_request(shadow):
    case_id = status_case(shadow)
    first = create(shadow, case_id, key="repeat-me-" + uuid4().hex)
    key = first.request.headers["Idempotency-Key"]
    again = create(shadow, case_id, key=key)
    assert again.status_code == 200
    assert again.json()["id"] == first.json()["id"]
    assert count_rows(shadow, "policy_decisions", first.json()["id"]) == 1
    other = status_case(shadow)
    assert create(shadow, other, key=key).json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"


@pytest.mark.parametrize("field", [
    "tenant_id", "requested_by", "approver_id", "approval_required",
    "request_policy_decision_id", "execution_policy_decision_id", "execution_id", "grant",
])
def test_the_body_cannot_carry_authoritative_fields(shadow, field):
    response = create(shadow, uuid4(), **{field: str(uuid4())})
    assert response.status_code == 422


def test_an_idempotency_key_is_required(shadow):
    response = shadow.request("POST", f"/api/playbooks/{PB001}/requests",
                              json={"case_id": str(uuid4())})
    assert response.status_code == 422


def test_an_ineligible_playbook_is_refused_and_writes_nothing(shadow):
    case_id = status_case(shadow)
    response = create(shadow, case_id, playbook="finops:PB-002")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PLAYBOOK_INELIGIBLE"
    assert response.json()["error"]["failures"]
    with scoped_tx(shadow.tenant) as conn:
        assert conn.execute(text("SELECT count(*) FROM playbook_requests WHERE case_id = :c"),
                            {"c": case_id}).scalar_one() == 0
    assert case_status(shadow, case_id) == "HUMAN_REVIEW"


def test_a_case_not_in_human_review_takes_no_request(shadow):
    case_id = status_case(shadow)
    assert create(shadow, case_id).status_code == 201
    second = create(shadow, case_id)
    assert second.status_code == 409 and second.json()["error"]["code"] == "CASE_STATE"


def test_an_unknown_or_inactive_playbook_is_404(shadow):
    assert create(shadow, status_case(shadow), playbook="finops:PB-999").status_code == 404


# POL-01 (AC-AUTHZ-04, demo step 9)


def test_requests_are_listed_per_case_and_tell_the_requester_apart(shadow, approver):
    """The screens' reads: a case's requests, and whether the reader made each one (POL-01
    hides approve and reject from the requester; the server still refuses them)."""
    case_id = status_case(shadow)
    request = create(shadow, case_id).json()
    assert request["requested_by_me"] is True
    listed = shadow.request("GET", "/api/playbook-requests", params={"case_id": str(case_id)})
    assert listed.status_code == 200, listed.text
    assert [r["id"] for r in listed.json()] == [request["id"]]
    assert shadow.request("GET", "/api/playbook-requests",
                          params={"case_id": str(case_id), "status": "REJECTED"}).json() == []
    seen = approver.request("GET", f"/api/playbook-requests/{request['id']}").json()
    assert seen["requested_by_me"] is False
    assert "playbook.requested" in [a["action"] for a in seen["audit_refs"]]


@pytest.mark.parametrize("route", ["approve", "reject"])
def test_the_requester_cannot_decide_their_own_request(shadow, route):
    both = Person(shadow, "domain_operator", "approver")
    request = create(both, status_case(shadow)).json()
    response = both.request("POST", f"/api/playbook-requests/{request['id']}/{route}",
                            json={"reason": "mine"})
    assert response.status_code == 403
    assert response.json()["error"] == {
        "code": "POLICY_DENIED", "policy": "POL-01",
        "message": "The requester cannot approve or reject their own request."}
    with scoped_tx(shadow.tenant) as conn:
        audit = conn.execute(
            text(
                "SELECT permission_key, decision, policy_rules FROM audit_logs "
                "WHERE action LIKE 'playbook.%_denied' AND detail->>'request_id' = :r"
            ),
            {"r": request["id"]},
        ).one()
    assert (audit.decision, list(audit.policy_rules)) == ("DENY", ["POL-01"])
    assert audit.permission_key == f"playbook.{route}"
    assert count_rows(shadow, "playbook_approvals", request["id"]) == 0


def test_a_reject_is_terminal_and_returns_the_case(shadow, approver):
    case_id = status_case(shadow)
    request = create(shadow, case_id).json()
    response = approver.request("POST", f"/api/playbook-requests/{request['id']}/reject",
                                json={"reason": "Not this payment."})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "REJECTED"
    assert response.json()["approval"]["decision"] == "REJECTED"
    assert case_status(shadow, case_id) == "HUMAN_REVIEW"
    assert approve(approver, request["id"]).status_code == 409


def test_a_reject_needs_a_reason(shadow, approver):
    request = create(shadow, status_case(shadow)).json()
    response = approver.request("POST", f"/api/playbook-requests/{request['id']}/reject",
                                json={})
    assert response.status_code == 422


# The execution stage


def test_an_approval_in_shadow_is_blocked_and_issues_nothing(shadow, approver, granted):
    """AC-AUTHZ-15, POL-06, demo step 10: approval never bypasses SHADOW."""
    request = create(shadow, status_case(shadow)).json()
    response = approve(approver, request["id"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "PRECONDITIONS_VALIDATED"
    assert body["approval"]["request_policy_decision_id"] == body["decisions"][0]["id"]
    blocked = body["decisions"][1]
    assert (blocked["stage"], blocked["decision"]) == ("EXECUTION", "POLICY_BLOCKED")
    assert blocked["conditions"]["policy"] == "POL-06"
    assert blocked["parent_decision_id"] == body["decisions"][0]["id"]
    assert body["execution"] is None
    assert count_rows(shadow, "execution_grants", request["id"]) == 0
    assert granted == []


def test_live_reevaluates_the_approved_request_and_binds_one_grant(shadow, approver, granted):
    """AC-PLY-08, AC-PLY-09, AC-AUTHZ-08, demo steps 12-13."""
    request = create(shadow, status_case(shadow)).json()
    approve(approver, request["id"])
    switched = put_mode(shadow, MERCHANT, "LIVE")
    assert switched.status_code == 200, switched.text
    assert request["id"] in switched.json()["reevaluated_requests"]

    body = shadow.request("GET", f"/api/playbook-requests/{request['id']}").json()
    assert body["status"] == "EXECUTION_CREATED"
    request_decision, blocked, allowed = body["decisions"]
    assert (blocked["decision"], allowed["decision"]) == ("POLICY_BLOCKED", "ALLOW")
    assert allowed["parent_decision_id"] == request_decision["id"]
    execution = body["execution"]
    assert execution["execution_policy_decision_id"] == allowed["id"]
    assert execution["request_policy_decision_id"] == request_decision["id"]
    assert execution["approval_id"] == body["approval"]["id"]
    assert execution["status"] == "GRANT_ISSUED"

    # The switch also re-evaluates requests earlier tests left blocked; take this one's grant.
    [sent] = [g for g in granted if str(g.execution_id) == execution["id"]]
    with scoped_tx(shadow.tenant) as conn:
        grant = conn.execute(text("SELECT * FROM execution_grants WHERE id = :id"),
                             {"id": sent.grant_id}).one()
    assert grant.token_hash == hashlib.sha256(sent.token.encode()).hexdigest()
    assert sent.token not in grant.token_hash
    assert str(grant.execution_id) == execution["id"] == str(sent.execution_id)
    assert str(grant.execution_policy_decision_id) == allowed["id"]
    assert str(grant.request_policy_decision_id) == request_decision["id"]
    assert str(grant.approval_id) == body["approval"]["id"]
    assert grant.target == body["target"]
    assert grant.idempotency_key == execution["idempotency_key"]
    assert grant.consumed_at is None and grant.expires_at > grant.issued_at
    assert list(grant.permission_keys) == gateway.GRANT_PERMISSIONS


def test_another_live_scope_does_not_unblock_this_one(shadow, approver, granted):
    """AC-EXEC-MODE-04: refunds.create LIVE leaves PB-001's merchant scope SHADOW."""
    assert put_mode(shadow, REFUND, "LIVE").status_code == 200
    body = approve(approver, create(shadow, status_case(shadow)).json()["id"]).json()
    assert body["decisions"][-1]["conditions"]["policy"] == "POL-06"
    assert body["execution"] is None and granted == []


def test_a_grant_is_refused_when_the_scope_returns_to_shadow_after_the_decision(
        shadow, approver, granted, monkeypatch):
    """Spec 9.5: the mode is checked again at grant issue."""
    assert put_mode(shadow, MERCHANT, "LIVE").status_code == 200
    granted.clear()  # Grants for requests earlier tests left blocked.
    real = gateway.in_shadow
    calls = count()

    def gate(conn, scope):
        return real(conn, scope) if next(calls) == 0 else True

    monkeypatch.setattr(gateway, "in_shadow", gate)
    body = approve(approver, create(shadow, status_case(shadow)).json()["id"]).json()
    assert body["execution"]["status"] == "POLICY_BLOCKED"
    assert count_rows(shadow, "execution_grants", body["id"]) == 0
    assert granted == []


@pytest.mark.parametrize("change", ["ineligible", "target"])
def test_a_changed_precondition_or_target_denies_and_returns_the_case(shadow, approver,
                                                                      granted, monkeypatch,
                                                                      change):
    """AC-EXEC-MODE-04: LIVE does not survive a changed intent."""
    assert put_mode(shadow, MERCHANT, "LIVE").status_code == 200
    granted.clear()  # Grants for requests earlier tests left blocked.
    case_id = status_case(shadow)
    request = create(shadow, case_id).json()
    verdict = (
        {"eligible": False, "failures": ["The merchant payment is no longer PENDING."],
         "target": None, "proof": {}, "evidence_ids": []}
        if change == "ineligible" else
        {"eligible": True, "failures": [], "target": {**request["target"], "payment_id": "X"},
         "proof": {}, "evidence_ids": []}
    )
    monkeypatch.setattr(gateway, "evaluate", lambda *args: verdict)
    body = approve(approver, request["id"]).json()
    denied = body["decisions"][-1]
    assert (denied["stage"], denied["decision"]) == ("EXECUTION", "DENY")
    assert denied["conditions"]["failures"]
    assert body["status"] == "DENIED" and body["execution"] is None
    assert case_status(shadow, case_id) == "HUMAN_REVIEW"
    assert granted == []


def test_without_required_approval_the_request_goes_straight_to_execution(shadow, granted):
    """AC-PLY-08: NOT_REQUIRED comes only from recorded tenant policy, and then approval_id
    is NULL on the execution and its grant."""
    with scoped_tx(shadow.tenant) as conn:
        version = conn.execute(
            text("SELECT coalesce(max(version), 0) + 1 FROM playbook_approval_policies "
                 "WHERE playbook_id = 'PB-001'")
        ).scalar_one()
        conn.execute(
            text(
                """
                INSERT INTO playbook_approval_policies (id, tenant_id, pack_namespace,
                    playbook_id, version, approval_policy, reason, set_by)
                VALUES (:id, :t, 'finops', 'PB-001', :v, 'NOT_REQUIRED', 'test', :u)
                """
            ),
            {"id": uuid4(), "t": shadow.tenant, "v": version, "u": shadow.principal.id},
        )
    try:
        assert put_mode(shadow, MERCHANT, "LIVE").status_code == 200
        body = create(shadow, status_case(shadow)).json()
        request_decision, allowed = body["decisions"]
        assert (request_decision["decision"], request_decision["approval_required"]) == (
            "ALLOW_PENDING_PRECONDITIONS", False)
        assert allowed["decision"] == "ALLOW"
        assert body["approval"] is None and body["execution"]["approval_id"] is None
        [sent] = [g for g in granted if str(g.execution_id) == body["execution"]["id"]]
        with scoped_tx(shadow.tenant) as conn:
            assert conn.execute(text("SELECT approval_id FROM execution_grants WHERE id = :g"),
                                {"g": sent.grant_id}).scalar_one() is None
    finally:
        with scoped_tx(shadow.tenant) as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO playbook_approval_policies (id, tenant_id, pack_namespace,
                        playbook_id, version, approval_policy, reason, set_by)
                    VALUES (:id, :t, 'finops', 'PB-001', :v, 'REQUIRED', 'restore', :u)
                    """
                ),
                {"id": uuid4(), "t": shadow.tenant, "v": version + 1,
                 "u": shadow.principal.id},
            )


def test_requests_and_decisions_are_audited_with_policy_ids(shadow, approver):
    """AC-AUTHZ-14."""
    request = create(shadow, status_case(shadow)).json()
    approve(approver, request["id"])
    with scoped_tx(shadow.tenant) as conn:
        rows = conn.execute(
            text(
                "SELECT action, permission_key, decision, policy_rules FROM audit_logs "
                "WHERE detail->>'request_id' = :r ORDER BY created_at"
            ),
            {"r": request["id"]},
        ).all()
    found = {r.action: (r.permission_key, r.decision, list(r.policy_rules)) for r in rows}
    assert found["playbook.requested"] == ("playbook.request", "NEEDS_APPROVAL", ["POL-11"])
    assert found["playbook.approved"] == ("playbook.approve", "ALLOW", ["POL-01"])
    assert found["playbook.execution_blocked"][1:] == ("POLICY_BLOCKED", ["POL-06"])


def test_another_tenant_cannot_read_or_approve_the_request(shadow):
    request = create(shadow, status_case(shadow)).json()
    other = Member()
    grant_set(other.tenant, other.principal.id, "approver")
    assert other.request("GET", f"/api/playbook-requests/{request['id']}").status_code == 404
    assert approve(other, request["id"]).status_code == 404
