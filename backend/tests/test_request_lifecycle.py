"""The request-lifecycle state machine and PB-001 over real evidence (task 5.9, spec 9.3).

The Python machine and the database trigger from migration 0016 allow exactly the same
transitions. A terminal request returns its case from ACTION_PENDING to HUMAN_REVIEW, and the
case and request state machines stay separate.
"""

import re
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from app.db import global_tx, scoped_tx
from app.domain_packs.playbooks import get_playbooks
from app.domain_packs.registry import resolve_entrypoint
from app.errors import ApiError
from app.playbooks import lifecycle
from app.playbooks.evidence import case_snapshot
from tests.chain_rows import insert_request
from tests.domain_rows import seed_domain_rows
from tests.test_paypal_mappings import PayPalTenant, paypal_capture, pending
from tests.test_rls import make_tenant, make_user

pytestmark = pytest.mark.db


def test_python_and_database_allow_the_same_request_transitions():
    with global_tx() as conn:
        source = conn.execute(
            text("SELECT pg_get_functiondef('guard_playbook_request'::regproc)")
        ).scalar_one()
    pairs = set(re.findall(r"\('([A-Z_]+)', '([A-Z_]+)'\)", source))
    assert pairs == set(lifecycle.REQUEST_TRANSITIONS)


def test_the_machine_has_one_path_to_execution_and_a_no_approval_shortcut():
    happy = ["CREATED", "REQUEST_POLICY_EVALUATED", "NEEDS_APPROVAL", "APPROVED",
             "PRECONDITIONS_VALIDATED", "EXECUTION_POLICY_ALLOWED", "EXECUTION_CREATED"]
    assert all(lifecycle.can_advance(a, b) for a, b in zip(happy, happy[1:], strict=False))
    assert lifecycle.can_advance("REQUEST_POLICY_EVALUATED", "PRECONDITIONS_VALIDATED")
    assert not lifecycle.can_advance("NEEDS_APPROVAL", "PRECONDITIONS_VALIDATED")
    for terminal in lifecycle.RETURNS_CASE:
        assert not [b for a, b in lifecycle.REQUEST_TRANSITIONS if a == terminal]


@pytest.fixture
def request_row() -> tuple[UUID, UUID, UUID]:
    owner = make_user()
    tenant = make_tenant(owner)
    case_id = seed_domain_rows(tenant, owner)["domain_cases"]
    with scoped_tx(tenant) as conn:
        for status in ("INVESTIGATING", "HUMAN_REVIEW", "ACTION_PENDING"):
            conn.execute(text("UPDATE domain_cases SET status = :s WHERE id = :id"),
                         {"s": status, "id": case_id})
        request_id = insert_request(conn, tenant, case_id, owner, f"k-{uuid4()}")
    return tenant, case_id, request_id


def _case_status(tenant: UUID, case_id: UUID) -> str:
    with scoped_tx(tenant) as conn:
        return conn.execute(
            text("SELECT status FROM domain_cases WHERE id = :id"), {"id": case_id}
        ).scalar_one()


def test_advance_walks_the_machine_and_leaves_the_case_alone(request_row):
    tenant, case_id, request_id = request_row
    with scoped_tx(tenant) as conn:
        state = lifecycle.advance(conn, request_id, "REQUEST_POLICY_EVALUATED",
                                  "NEEDS_APPROVAL", "APPROVED")
    assert state == "APPROVED"
    assert _case_status(tenant, case_id) == "ACTION_PENDING"


def test_advance_refuses_a_skipped_state(request_row):
    tenant, _, request_id = request_row
    with pytest.raises(ApiError) as refused:
        with scoped_tx(tenant) as conn:
            lifecycle.advance(conn, request_id, "APPROVED")
    assert refused.value.code == "REQUEST_STATE"


@pytest.mark.parametrize("terminal", ["REJECTED", "EXPIRED", "CANCELLED"])
def test_a_terminal_request_returns_the_case_to_human_review(request_row, terminal):
    tenant, case_id, request_id = request_row
    with scoped_tx(tenant) as conn:
        lifecycle.advance(conn, request_id, "REQUEST_POLICY_EVALUATED", "NEEDS_APPROVAL",
                          terminal)
    assert _case_status(tenant, case_id) == "HUMAN_REVIEW"


def test_a_denied_request_returns_the_case_to_human_review(request_row):
    tenant, case_id, request_id = request_row
    with scoped_tx(tenant) as conn:
        lifecycle.advance(conn, request_id, "REQUEST_POLICY_EVALUATED", "DENIED")
    assert _case_status(tenant, case_id) == "HUMAN_REVIEW"


# PB-001 against a real PayPal event and merchant row


@pytest.fixture(scope="module")
def paypal() -> PayPalTenant:
    return PayPalTenant()


def _status_case(tenant: PayPalTenant, capture: str) -> dict:
    [case] = [c for c in tenant.cases(case_type="STATUS_MISMATCH")
              if _captures(tenant, c) == {capture}]
    return case


def _captures(tenant: PayPalTenant, case: dict) -> set[str]:
    with scoped_tx(tenant.tenant) as conn:
        return set(conn.execute(
            text(
                """
                SELECT DISTINCT normalized_value FROM property_observations
                WHERE entity_id = ANY (CAST(:ids AS uuid[]))
                  AND property_iri = 'finops:providerReference'
                """
            ),
            {"ids": case["entity_ids"]},
        ).scalars())


def _evaluate(tenant: PayPalTenant, case: dict) -> dict:
    check = resolve_entrypoint(get_playbooks()["finops:PB-001"].definition.preconditions)
    with scoped_tx(tenant.tenant) as conn:
        return check(case_snapshot(conn, UUID(case["id"])), {})


def test_pb001_is_eligible_for_a_completed_capture_and_a_pending_payment(paypal):
    paypal.event("PAYMENT.CAPTURE.COMPLETED", paypal_capture("CAP-PB1-1", "ORD-PB1-1"))
    paypal.merchant_payment(pending("CAP-PB1-1", "ORD-PB1-1"))
    paypal.run()
    result = _evaluate(paypal, _status_case(paypal, "CAP-PB1-1"))
    assert result["eligible"] is True, result["failures"]
    assert result["target"]["payment_id"] == "CAP-PB1-1"
    assert result["target"]["merchant_source_id"] == str(paypal.sources["merchant"])
    assert (result["proof"]["currency"], result["proof"]["order_reference"]) == ("USD",
                                                                                  "ORD-PB1-1")


def test_pb001_is_ineligible_when_the_amounts_disagree(paypal):
    paypal.event("PAYMENT.CAPTURE.COMPLETED", paypal_capture("CAP-PB1-2", "ORD-PB1-2"))
    paypal.merchant_payment(pending("CAP-PB1-2", "ORD-PB1-2") | {"amount": "90.00"})
    paypal.run()
    result = _evaluate(paypal, _status_case(paypal, "CAP-PB1-2"))
    assert result["eligible"] is False
    assert any("disagree" in f for f in result["failures"])


def test_a_snapshot_is_tenant_scoped(paypal):
    paypal.event("PAYMENT.CAPTURE.COMPLETED", paypal_capture("CAP-PB1-3", "ORD-PB1-3"))
    paypal.merchant_payment(pending("CAP-PB1-3", "ORD-PB1-3"))
    paypal.run()
    case = _status_case(paypal, "CAP-PB1-3")
    other = make_tenant(make_user())
    with scoped_tx(other) as conn:
        assert case_snapshot(conn, UUID(case["id"])) is None
