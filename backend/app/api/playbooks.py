"""Playbook requests, approvals, execution reads and audit (spec 9, POL-01, POL-09).

Humans have no execution route here. Bodies must never accept tenant_id, requested_by,
approver_id, approval_required, policy-decision IDs, execution_id or grants (spec 12.1).
"""

from uuid import UUID

from fastapi import APIRouter

from app import audit
from app.authz.guard import TenantContext, require
from app.errors import not_implemented

router = APIRouter(prefix="/api", tags=["playbooks"])

# TODO(G5 exit Rishav): the G5 evidence list in spec 17 passes as automated tests: AC-INV-01,
# AC-PLY-08..11, AC-AUTHZ-06/08/15/16/17, AC-REL-01..12, AC-MERCH-01..07 and POL-01/06/09/14,
# plus AC-PP-08 and the PB-002 parts of AC-PLY-08. If the safety suite is not green by Day 3
# hour 7, no attestation is issued and the demo stops at step 10 in SHADOW.

# TODO(G5 5.1 Rishav): migration 0016 adds playbook_requests, policy_decisions (stage REQUEST
# or EXECUTION, immutable), playbook_approvals (referencing the request and its REQUEST-stage
# decision), playbook_executions (own id, referencing the request and both decisions),
# execution_grants and execution_action_attempts (spec 6.3, 9.3, 9.4). Composite foreign keys
# on (tenant_id, id) so a cross-tenant reference fails; approval_id is nullable only when the
# REQUEST-stage decision says approval is not required, enforced by constraint or trigger
# (AC-PLY-11). Decisions, approvals and attempts are append-only (SELECT, INSERT); every table
# gets enable_tenant_rls and an explicit GRANT. Keep test_rls.py green.
# TODO(G5 5.12 Suprava): the safety suite, as tests that fail CI: AC-MERCH-01..07 against the
# 5.5 adapter, AC-AUTHZ-06/08/15/16/17 (Worker-only keys, grant binding, SHADOW, POL-14,
# humans refused by the Worker), and AC-PLY-08..11 against these routes and the 5.1 tables.
# Declare its categories in packaged data (spec 9.7.2) so 5.4 can refuse an empty one, and
# trace each criterion in the acceptance sheet (6.5).


# TODO(G5 5.2 Rishav): a human with playbook.request creates the request for one case; the body
# names the case and the playbook's declared parameters only (spec 12.1). In one transaction:
# the request, its immutable REQUEST-stage decision from tenant policy (NEEDS_APPROVAL or
# ALLOW_PENDING_PRECONDITIONS; PB-002 always NEEDS_APPROVAL, AC-PLY-08) and the case moved to
# ACTION_PENDING (spec 9.3). A repeated tenant-scoped idempotency key returns the prior request
# (AC-PLY-04). Adjust test_held_permission_passes_the_gate and the 501 check.
# TODO(G5 5.9 Swapnaneel): the request-lifecycle state machine this route starts (spec 9.3):
# CREATED -> REQUEST_POLICY_EVALUATED -> NEEDS_APPROVAL -> APPROVED -> PRECONDITIONS_VALIDATED
# -> EXECUTION_POLICY_ALLOWED -> EXECUTION_CREATED, with the NOT_REQUIRED shortcut and the
# terminal DENIED, REJECTED, EXPIRED and CANCELLED states. A terminal state returns the case to
# HUMAN_REVIEW. Keep it apart from case and execution state.
@router.post("/playbooks/{playbook_id}/requests")
def create_playbook_request(
    playbook_id: str,
    ctx: TenantContext = require("playbook.request", scope=("playbook", "playbook_id")),
) -> None:
    raise not_implemented("5.2")


@router.get("/playbook-requests/{request_id}")
def read_playbook_request(request_id: str, ctx: TenantContext = require("playbook.read")) -> None:
    raise not_implemented("5.2")


# TODO(G5 5.2 Rishav): approve and reject. POL-01: the approver is a different human from the
# requester, and the decision references the request and its REQUEST-stage decision. On
# approval the gateway revalidates preconditions, target, source authority, connector
# capability, exact-scope mode (5.3) and POL-14 (5.4), then writes a separate immutable
# EXECUTION-stage decision; only ALLOW creates the execution row, and after commit a
# single-use grant bound to execution, both decisions, the conditional approval, action, target
# and idempotency key (POL-09, AC-AUTHZ-08). Rejection is terminal. Audit both as critical.
@router.post("/playbook-requests/{request_id}/approve")
def approve_playbook_request(
    request_id: str, ctx: TenantContext = require("playbook.approve")
) -> None:
    raise not_implemented("5.2")


@router.post("/playbook-requests/{request_id}/reject")
def reject_playbook_request(
    request_id: str, ctx: TenantContext = require("playbook.reject")
) -> None:
    raise not_implemented("5.2")


# TODO(G5 5.6 Rahul): read one execution with its state, both decision IDs, the approval if
# any, its attempts and the readback evidence that verified it.
@router.get("/executions/{execution_id}")
def read_execution(execution_id: str, ctx: TenantContext = require("execution.read")) -> None:
    raise not_implemented("5.6")


@router.get("/audit/{audit_id}")
def read_audit(audit_id: UUID, ctx: TenantContext = require("audit.read")) -> dict:
    return audit.read_audit(ctx, audit_id)
