"""The execution chain's schema rules, as the runtime role (task 5.1, spec 6.3 and 9.3).

AC-PLY-09: an immutable REQUEST decision exists before any approval; the approval references
the request and that decision; a separate EXECUTION-stage ALLOW follows; the execution has its
own id and references both. AC-PLY-11: approval_id is NULL only when the REQUEST decision says
approval is not required, and cross-tenant references fail. AC-PLY-10: attempts are append-only.
"""

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.db import scoped_tx
from tests.chain_rows import (
    TARGET,
    insert_approval,
    insert_decision,
    insert_execution,
    insert_grant,
    insert_request,
    seed_chain,
)
from tests.test_rls import make_tenant, make_user

pytestmark = pytest.mark.db

CHAIN_TABLES = (
    "playbook_requests", "policy_decisions", "playbook_approvals", "executions",
    "execution_grants", "execution_action_attempts", "playbook_approval_policies",
)
APPEND_ONLY = (
    "playbook_approval_policies", "policy_decisions", "playbook_approvals",
    "execution_action_attempts",
)


@pytest.fixture
def people() -> tuple[UUID, UUID]:
    return make_user(), make_user()


@pytest.fixture
def tenant(people) -> UUID:
    return make_tenant(people[0])


@pytest.fixture
def chain(tenant, people) -> dict:
    return seed_chain(tenant, *people)


def _new_request(tenant: UUID, chain: dict, requester: UUID, *, approval_required: bool):
    """A second request on the same case, with its REQUEST decision."""
    with scoped_tx(tenant) as conn:
        request_id = insert_request(conn, tenant, chain["case"], requester, f"k-{uuid4()}")
        first = "NEEDS_APPROVAL" if approval_required else "ALLOW_PENDING_PRECONDITIONS"
        decision = insert_decision(conn, tenant, request_id, "REQUEST", first,
                                   approval_required=approval_required)
    return request_id, decision


def _raises(tenant: UUID, match: str, statement, *args, **kwargs):
    with pytest.raises((DBAPIError, IntegrityError), match=match):
        with scoped_tx(tenant) as conn:
            statement(conn, *args, **kwargs)


# AC-PLY-09: the full chain, in order


def test_a_full_chain_with_approval_is_accepted(tenant, chain):
    with scoped_tx(tenant) as conn:
        execution = conn.execute(
            text("SELECT * FROM executions WHERE id = :id"), {"id": chain["execution"]}
        ).one()
        approval = conn.execute(
            text("SELECT * FROM playbook_approvals WHERE id = :id"), {"id": chain["approval"]}
        ).one()
    assert execution.tenant_id == tenant and execution.id != chain["request"]
    assert (execution.request_policy_decision_id, execution.execution_policy_decision_id) == (
        chain["request_decision"], chain["execution_decision"]
    )
    assert (approval.request_id, approval.request_policy_decision_id) == (
        chain["request"], chain["request_decision"]
    )


def test_a_chain_without_approval_has_a_null_approval_id(tenant, people):
    chain = seed_chain(tenant, *people, approval_required=False)
    with scoped_tx(tenant) as conn:
        assert conn.execute(
            text("SELECT approval_id FROM executions WHERE id = :id"), {"id": chain["execution"]}
        ).scalar_one() is None


def test_a_request_has_one_request_stage_decision(tenant, chain):
    _raises(tenant, "policy_decisions_one_request_stage", insert_decision, tenant,
            chain["request"], "REQUEST", "NEEDS_APPROVAL", approval_required=True)


def test_a_request_decision_has_no_parent(tenant, chain, people):
    request_id, decision = _new_request(tenant, chain, people[0], approval_required=True)
    _raises(tenant, "check", insert_decision, tenant, request_id, "REQUEST", "DENY",
            approval_required=True, parent=decision)


def test_an_execution_decision_follows_its_own_requests_decision(tenant, chain, people):
    other_request, _ = _new_request(tenant, chain, people[0], approval_required=True)
    _raises(tenant, "follows its own request decision", insert_decision, tenant, other_request,
            "EXECUTION", "ALLOW", approval_required=True, parent=chain["request_decision"])


def test_an_execution_decision_cannot_change_the_approval_requirement(tenant, chain):
    _raises(tenant, "keeps the approval requirement", insert_decision, tenant, chain["request"],
            "EXECUTION", "ALLOW", approval_required=False, parent=chain["request_decision"])


def test_needs_approval_always_means_approval_required(tenant, chain, people):
    with pytest.raises(IntegrityError, match="check"):
        with scoped_tx(tenant) as conn:
            request_id = insert_request(conn, tenant, chain["case"], people[0], "k-x")
            insert_decision(conn, tenant, request_id, "REQUEST", "NEEDS_APPROVAL",
                            approval_required=False)


# Approvals: POL-01 and the REQUEST decision they answer


def test_the_requester_cannot_approve_their_own_request(tenant, chain, people):
    request_id, decision = _new_request(tenant, chain, people[0], approval_required=True)
    _raises(tenant, "POL-01", insert_approval, tenant, request_id, decision, people[0])


def test_an_approval_needs_a_decision_that_requires_one(tenant, chain, people):
    request_id, decision = _new_request(tenant, chain, people[0], approval_required=False)
    _raises(tenant, "needs approval", insert_approval, tenant, request_id, decision, people[1])


def test_one_decision_slot_per_request_decision(tenant, chain, people):
    _raises(tenant, "duplicate key", insert_approval, tenant, chain["request"],
            chain["request_decision"], people[1])


# AC-PLY-11: the conditional approval relationship on executions and grants


def _execution_for(tenant, request_id, request_decision, approval_id, *, approval_required,
                   execution_decision="ALLOW"):
    with scoped_tx(tenant) as conn:
        decision = insert_decision(conn, tenant, request_id, "EXECUTION", execution_decision,
                                   approval_required=approval_required, parent=request_decision)
    return lambda conn: insert_execution(conn, tenant, request_id, request_decision, decision,
                                         approval_id, f"e-{uuid4()}")


def test_required_approval_cannot_be_omitted(tenant, chain, people):
    request_id, decision = _new_request(tenant, chain, people[0], approval_required=True)
    _raises(tenant, "approval is required",
            _execution_for(tenant, request_id, decision, None, approval_required=True))


def test_a_rejected_approval_does_not_authorize(tenant, chain, people):
    request_id, decision = _new_request(tenant, chain, people[0], approval_required=True)
    with scoped_tx(tenant) as conn:
        rejected = insert_approval(conn, tenant, request_id, decision, people[1], "REJECTED")
    _raises(tenant, "approval is required",
            _execution_for(tenant, request_id, decision, rejected, approval_required=True))


def test_another_requests_approval_does_not_authorize(tenant, chain, people):
    request_id, decision = _new_request(tenant, chain, people[0], approval_required=True)
    _raises(tenant, "approval is required",
            _execution_for(tenant, request_id, decision, chain["approval"],
                           approval_required=True))


def test_no_approval_may_be_attached_when_none_is_required(tenant, chain, people):
    request_id, decision = _new_request(tenant, chain, people[0], approval_required=False)
    _raises(tenant, "approval_id is NULL",
            _execution_for(tenant, request_id, decision, chain["approval"],
                           approval_required=False))


@pytest.mark.parametrize("outcome", ["DENY", "POLICY_BLOCKED"])
def test_only_an_execution_stage_allow_creates_an_execution(tenant, chain, people, outcome):
    request_id, decision = _new_request(tenant, chain, people[0], approval_required=False)
    _raises(tenant, "EXECUTION-stage ALLOW",
            _execution_for(tenant, request_id, decision, None, approval_required=False,
                           execution_decision=outcome))


def test_a_request_decision_cannot_stand_in_for_the_execution_decision(tenant, chain, people):
    request_id, decision = _new_request(tenant, chain, people[0], approval_required=False)
    _raises(tenant, "EXECUTION-stage ALLOW", lambda conn: insert_execution(
        conn, tenant, request_id, decision, decision, None, "e-same"))


def test_one_execution_per_request(tenant, chain):
    _raises(tenant, "duplicate key", lambda conn: insert_execution(
        conn, tenant, chain["request"], chain["request_decision"], chain["execution_decision"],
        chain["approval"], "e-again"))


def test_a_grant_is_bound_to_exactly_its_execution(tenant, chain):
    _raises(tenant, "bound to exactly its execution", insert_grant, tenant, chain,
            target='{"payment_id": "PAY-2"}')


def test_a_grant_without_its_approval_is_refused(tenant, chain):
    _raises(tenant, "bound to exactly its execution", insert_grant, tenant,
            {**chain, "approval": None})


# AC-AUTHZ-08 at the schema: single use, expiring, frozen


def _consume(conn, grant_id):
    result = conn.execute(
        text("UPDATE execution_grants SET consumed_at = now() WHERE id = :id"), {"id": grant_id}
    )
    assert result.rowcount == 1


def test_a_grant_is_consumed_once(tenant, chain):
    with scoped_tx(tenant) as conn:
        _consume(conn, chain["grant"])
    _raises(tenant, "consumed once", _consume, chain["grant"])


def test_an_expired_grant_cannot_be_consumed(tenant, chain):
    with scoped_tx(tenant) as conn:
        expired = insert_grant(conn, tenant, chain, expires_in=timedelta(microseconds=1))
    _raises(tenant, "unexpired", _consume, expired)


def test_consuming_a_grant_changes_nothing_else(tenant, chain):
    _raises(tenant, "changes nothing else", lambda conn: conn.execute(
        text("UPDATE execution_grants SET consumed_at = now(), target = '{}' WHERE id = :id"),
        {"id": chain["grant"]}))


def test_a_grant_is_issued_unconsumed(tenant, chain):
    _raises(tenant, "issued unconsumed", lambda conn: conn.execute(text(
        """
        INSERT INTO execution_grants (id, tenant_id, execution_id, request_id,
            request_policy_decision_id, execution_policy_decision_id, approval_id,
            playbook_version, connector_id, action_capability, target, idempotency_key,
            permission_keys, token_hash, expires_at, consumed_at)
        SELECT gen_random_uuid(), tenant_id, execution_id, request_id,
            request_policy_decision_id, execution_policy_decision_id, approval_id,
            playbook_version, connector_id, action_capability, target, idempotency_key,
            permission_keys, repeat('c', 64), expires_at, now()
        FROM execution_grants WHERE id = :id
        """), {"id": chain["grant"]}))


# Spec 9.3 state machines


@pytest.mark.parametrize(("start", "to"), [
    ("CREATED", "APPROVED"),
    ("CREATED", "EXECUTION_CREATED"),
])
def test_a_request_moves_only_along_its_machine(tenant, chain, people, start, to):
    with scoped_tx(tenant) as conn:
        request_id = insert_request(conn, tenant, chain["case"], people[0], f"k-{uuid4()}")
    _raises(tenant, "request transition", lambda conn: conn.execute(
        text("UPDATE playbook_requests SET status = :to WHERE id = :id"),
        {"to": to, "id": request_id}))


def test_a_request_keeps_its_intent(tenant, chain):
    _raises(tenant, "keeps its intent", lambda conn: conn.execute(
        text("UPDATE playbook_requests SET target = '{}' WHERE id = :id"),
        {"id": chain["request"]}))


def test_a_request_starts_created(tenant, chain, people):
    _raises(tenant, "starts CREATED", lambda conn: conn.execute(text(
        """
        INSERT INTO playbook_requests (id, tenant_id, case_id, pack_namespace, pack_version,
            playbook_id, playbook_version, connector_id, action_capability, requested_by,
            parameters, target, evidence_refs, idempotency_key, status)
        VALUES (gen_random_uuid(), :t, :case, 'finops', '0.2.0', 'PB-001', '1.0.0', 'c', 'a',
            :user, '{}', :target, '[]', 'k-approved', 'APPROVED')
        """), {"t": tenant, "case": chain["case"], "user": people[0], "target": TARGET}))


def test_an_execution_moves_only_along_its_machine(tenant, chain):
    with scoped_tx(tenant) as conn:
        for status in ("GRANT_ISSUED", "EXECUTING", "VERIFYING", "SUCCEEDED"):
            conn.execute(text("UPDATE executions SET status = :s WHERE id = :id"),
                         {"s": status, "id": chain["execution"]})
    _raises(tenant, "execution transition", lambda conn: conn.execute(
        text("UPDATE executions SET status = 'EXECUTING' WHERE id = :id"),
        {"id": chain["execution"]}))


def test_an_execution_cannot_skip_its_grant(tenant, chain):
    _raises(tenant, "execution transition", lambda conn: conn.execute(
        text("UPDATE executions SET status = 'SUCCEEDED' WHERE id = :id"),
        {"id": chain["execution"]}))


def test_an_execution_keeps_its_authority(tenant, chain):
    _raises(tenant, "keeps its authority", lambda conn: conn.execute(
        text("UPDATE executions SET approval_id = NULL WHERE id = :id"),
        {"id": chain["execution"]}))


# AC-PLY-10: attempts carry the full binding and are append-only


def test_an_attempt_records_its_binding(tenant, chain):
    with scoped_tx(tenant) as conn:
        conn.execute(text(
            """
            INSERT INTO execution_action_attempts (id, tenant_id, execution_id, sequence,
                connector_id, action_capability, target, idempotency_key, status)
            VALUES (gen_random_uuid(), :t, :e, 1, 'internal-merchant',
                'merchant.payment_status.correct', :target, :key, 'STARTED')
            """), {"t": tenant, "e": chain["execution"], "target": TARGET, "key": chain["key"]})
    _raises(tenant, "duplicate key", lambda conn: conn.execute(text(
        """
        INSERT INTO execution_action_attempts (id, tenant_id, execution_id, sequence,
            connector_id, action_capability, target, idempotency_key, status)
        VALUES (gen_random_uuid(), :t, :e, 1, 'c', 'a', '{}', 'k', 'STARTED')
        """), {"t": tenant, "e": chain["execution"]}))


@pytest.mark.parametrize("table", APPEND_ONLY)
@pytest.mark.parametrize("statement", ["UPDATE {t} SET tenant_id = tenant_id", "DELETE FROM {t}"])
def test_append_only_chain_tables_refuse_change(tenant, chain, table, statement):
    _raises(tenant, "permission denied",
            lambda conn: conn.execute(text(statement.format(t=table))))


@pytest.mark.parametrize("table", CHAIN_TABLES)
def test_chain_rows_cannot_be_deleted(tenant, chain, table):
    _raises(tenant, "permission denied", lambda conn: conn.execute(text(f"DELETE FROM {table}")))


# AC-PLY-11 and AC-DB-01: no cross-tenant reference, no cross-tenant read


@pytest.fixture
def their_chain() -> dict:
    owner, approver = make_user(), make_user()
    other = make_tenant(owner)
    return seed_chain(other, owner, approver)


def test_tenant_a_sees_none_of_tenant_bs_chain(tenant, chain, their_chain):
    with scoped_tx(tenant) as conn:
        for table in CHAIN_TABLES:
            rows = conn.execute(text(f"SELECT tenant_id FROM {table}")).scalars().all()
            assert set(rows) <= {tenant}, table


CROSS_TENANT = {
    "request on their case": lambda conn, t, mine, theirs, user: insert_request(
        conn, t, theirs["case"], user, "k-cross"),
    "decision on their request": lambda conn, t, mine, theirs, user: insert_decision(
        conn, t, theirs["request"], "REQUEST", "DENY", approval_required=True),
    "approval of their decision": lambda conn, t, mine, theirs, user: insert_approval(
        conn, t, mine["request"], theirs["request_decision"], user),
    "execution under their decision": lambda conn, t, mine, theirs, user: insert_execution(
        conn, t, mine["request"], mine["request_decision"], theirs["execution_decision"],
        mine["approval"], "k-cross"),
    "grant for their execution": lambda conn, t, mine, theirs, user: insert_grant(
        conn, t, {**mine, "execution": theirs["execution"]}),
}


@pytest.mark.parametrize("statement", CROSS_TENANT.values(), ids=CROSS_TENANT.keys())
def test_tenant_a_cannot_reference_tenant_bs_chain(tenant, chain, their_chain, people,
                                                    statement):
    # A trigger that cannot see the other tenant's row refuses before the foreign key does.
    with pytest.raises((IntegrityError, DBAPIError)):
        with scoped_tx(tenant) as conn:
            statement(conn, tenant, chain, their_chain, people[1])
