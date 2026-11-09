"""Tenant administration, ownership and execution mode (spec 3, 9.5)."""

from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from app.authz.guard import TenantContext, require
from app.errors import not_implemented
from app.tenancy import ownership

router = APIRouter(prefix="/api/tenants/{tenant_id}", tags=["tenants"])


class TransferBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_owner_user_id: UUID


# TODO(G1 1.5 Rishav): high-entropy token stored hashed with expiry; POL-02 grant ceiling; no owner
# or platform sets (POL-13). Failed delivery stays retryable, activates nothing (AC-IAM-09); audit.
@router.post("/invitations")
def create_invitation(ctx: TenantContext = require("member.invite")) -> None:
    raise not_implemented("1.5")


@router.get("/invitations")
def list_invitations(ctx: TenantContext = require("member.invitation.manage")) -> None:
    raise not_implemented("1.5")


# TODO(G1 1.5 Rishav): rotate the token so prior links fail closed (spec 3.1); audit.
@router.post("/invitations/{invitation_id}/resend")
def resend_invitation(
    invitation_id: str, ctx: TenantContext = require("member.invitation.manage")
) -> None:
    raise not_implemented("1.5")


# TODO(G1 1.5 Rishav): a revoked token fails closed (AC-IAM-05); audit (AC-IAM-10).
@router.post("/invitations/{invitation_id}/revoke")
def revoke_invitation(
    invitation_id: str, ctx: TenantContext = require("member.invitation.manage")
) -> None:
    raise not_implemented("1.5")


# TODO(G1 1.12 Suprava): the stub is tagged 1.12, but the plan scopes 1.12 to screens. Agree who
# builds this read, which returns members and effective grants (spec 12.2).
@router.get("/members")
def list_members(ctx: TenantContext = require("member.read")) -> None:
    raise not_implemented("1.12")


# TODO(G1 1.5 Rishav): enforce POL-02, 03, 04, 05 and 13; takes effect on the next request; audit.
@router.patch("/members/{user_id}/grants")
def update_member_grants(user_id: str, ctx: TenantContext = require("member.role.assign")) -> None:
    raise not_implemented("1.5")


# TODO(G1 1.5 Rishav): set status to REMOVED and revoke grants (the app role has no DELETE).
# POL-04 protects the owner and the last member.role.assign holder; audit.
# TODO(G1 1.10 Suprava): add suspend-member (spec 12.2 omits it). Appendix A has no suspend key,
# so agree which existing key guards it; a new key needs a spec revision.
@router.delete("/members/{user_id}")
def remove_member(user_id: str, ctx: TenantContext = require("member.remove")) -> None:
    raise not_implemented("1.5")


@router.post("/ownership-transfer")
def transfer_ownership(
    body: TransferBody, ctx: TenantContext = require("tenant.ownership.transfer")
) -> dict:
    return ownership.transfer_ownership(ctx, body.new_owner_user_id)


@router.put("/execution-mode")
def set_execution_mode(ctx: TenantContext = require("execution_mode.manage")) -> None:
    raise not_implemented("5.3")


@router.get("/execution-readiness")
def execution_readiness(ctx: TenantContext = require("execution.read")) -> None:
    raise not_implemented("5.4")
