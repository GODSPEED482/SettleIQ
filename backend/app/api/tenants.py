"""Tenant administration, ownership and execution mode (spec 3, 9.5)."""

from fastapi import APIRouter

from app.authz.guard import TenantContext, require
from app.errors import not_implemented

router = APIRouter(prefix="/api/tenants/{tenant_id}", tags=["tenants"])


@router.post("/invitations")
def create_invitation(ctx: TenantContext = require("member.invite")) -> None:
    raise not_implemented("1.5")


@router.get("/invitations")
def list_invitations(ctx: TenantContext = require("member.invitation.manage")) -> None:
    raise not_implemented("1.5")


@router.post("/invitations/{invitation_id}/resend")
def resend_invitation(
    invitation_id: str, ctx: TenantContext = require("member.invitation.manage")
) -> None:
    raise not_implemented("1.5")


@router.post("/invitations/{invitation_id}/revoke")
def revoke_invitation(
    invitation_id: str, ctx: TenantContext = require("member.invitation.manage")
) -> None:
    raise not_implemented("1.5")


@router.get("/members")
def list_members(ctx: TenantContext = require("member.read")) -> None:
    raise not_implemented("1.12")


@router.patch("/members/{user_id}/grants")
def update_member_grants(user_id: str, ctx: TenantContext = require("member.role.assign")) -> None:
    raise not_implemented("1.5")


@router.delete("/members/{user_id}")
def remove_member(user_id: str, ctx: TenantContext = require("member.remove")) -> None:
    raise not_implemented("1.5")


@router.post("/ownership-transfer")
def transfer_ownership(ctx: TenantContext = require("tenant.ownership.transfer")) -> None:
    raise not_implemented("1.4")


@router.put("/execution-mode")
def set_execution_mode(ctx: TenantContext = require("execution_mode.manage")) -> None:
    raise not_implemented("5.3")


@router.get("/execution-readiness")
def execution_readiness(ctx: TenantContext = require("execution.read")) -> None:
    raise not_implemented("5.4")
