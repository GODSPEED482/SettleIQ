"""Playbook requests, approvals, execution reads and audit (spec 9, POL-01, POL-09).

Humans have no execution route here. Bodies must never accept tenant_id, requested_by,
approver_id, approval_required, policy-decision IDs, execution_id or grants (spec 12.1).
"""

from uuid import UUID

from fastapi import APIRouter, Header, Response

from app import audit
from app.authz.guard import TenantContext, require
from app.execution import worker
from app.playbooks import gateway

router = APIRouter(prefix="/api", tags=["playbooks"])

# TODO(G5 exit Rishav): the G5 evidence list in spec 17 passes as automated tests: AC-INV-01,
# AC-PLY-08..11, AC-AUTHZ-06/08/15/16/17, AC-REL-01..12, AC-MERCH-01..07 and POL-01/06/09/14,
# plus AC-PP-08 and the PB-002 parts of AC-PLY-08. If the safety suite is not green by Day 3
# hour 7, no attestation is issued and the demo stops at step 10 in SHADOW.


@router.post("/playbooks/{playbook_id}/requests", status_code=201)
def create_playbook_request(
    playbook_id: str,
    body: gateway.RequestBody,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200,
                                  pattern=r"^[!-~]+$"),
    ctx: TenantContext = require("playbook.request", scope=("playbook", "playbook_id")),
) -> dict:
    """Create one request and its REQUEST-stage decision. A repeated Idempotency-Key returns
    the prior request with 200."""
    view, created = gateway.create(ctx, playbook_id, body, idempotency_key)
    if not created:
        response.status_code = 200
    return view


@router.get("/playbook-requests")
def list_playbook_requests(
    case_id: UUID | None = None, status: str | None = None,
    ctx: TenantContext = require("playbook.read"),
) -> list[dict]:
    """Requests newest first, each with its decisions, approval and execution."""
    return gateway.list_requests(ctx, case_id, status)


@router.get("/playbook-requests/{request_id}")
def read_playbook_request(
    request_id: UUID, ctx: TenantContext = require("playbook.read")
) -> dict:
    return gateway.read(ctx, request_id)


@router.post("/playbook-requests/{request_id}/approve")
def approve_playbook_request(
    request_id: UUID,
    body: gateway.ApproveBody | None = None,
    ctx: TenantContext = require("playbook.approve"),
) -> dict:
    return gateway.approve(ctx, request_id, body)


@router.post("/playbook-requests/{request_id}/reject")
def reject_playbook_request(
    request_id: UUID, body: gateway.RejectBody,
    ctx: TenantContext = require("playbook.reject"),
) -> dict:
    return gateway.reject(ctx, request_id, body)


@router.get("/executions/{execution_id}")
def read_execution(
    execution_id: UUID, ctx: TenantContext = require("execution.read")
) -> dict:
    """One execution with its state, both decision IDs, the approval if any, every attempt and
    the readback evidence that verified it."""
    return worker.read(ctx, execution_id)


@router.get("/audit/{audit_id}")
def read_audit(audit_id: UUID, ctx: TenantContext = require("audit.read")) -> dict:
    return audit.read_audit(ctx, audit_id)
