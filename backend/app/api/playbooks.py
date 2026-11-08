"""Playbook requests, approvals, execution reads and audit (spec 9, POL-01, POL-09).

Humans have no execution route here. Bodies must never accept tenant_id, requested_by,
approver_id, approval_required, policy-decision IDs, execution_id or grants (spec 12.1).
"""

from fastapi import APIRouter

from app.authz.guard import TenantContext, require
from app.errors import not_implemented

router = APIRouter(prefix="/api", tags=["playbooks"])


@router.post("/playbooks/{playbook_id}/requests")
def create_playbook_request(
    playbook_id: str,
    ctx: TenantContext = require("playbook.request", scope=("playbook", "playbook_id")),
) -> None:
    raise not_implemented("5.2")


@router.get("/playbook-requests/{request_id}")
def read_playbook_request(request_id: str, ctx: TenantContext = require("playbook.read")) -> None:
    raise not_implemented("5.2")


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


@router.get("/executions/{execution_id}")
def read_execution(execution_id: str, ctx: TenantContext = require("execution.read")) -> None:
    raise not_implemented("5.6")


@router.get("/audit/{audit_id}")
def read_audit(audit_id: str, ctx: TenantContext = require("audit.read")) -> None:
    raise not_implemented("1.4")
