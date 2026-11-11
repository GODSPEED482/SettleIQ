"""Tenant administration, ownership and execution mode (spec 3, 9.5)."""

from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from app.authz.guard import TenantContext, require
from app.execution import mode, readiness
from app.mail import MailSender, get_mail_sender
from app.tenancy import invitations, members, ownership

router = APIRouter(prefix="/api/tenants/{tenant_id}", tags=["tenants"])


class TransferBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_owner_user_id: UUID


@router.post("/invitations", status_code=201)
def create_invitation(
    body: invitations.InviteBody,
    mail: MailSender = Depends(get_mail_sender),
    ctx: TenantContext = require("member.invite"),
) -> dict:
    return invitations.create_invitation(ctx, body, mail)


@router.get("/invitations")
def list_invitations(ctx: TenantContext = require("member.invitation.manage")) -> list[dict]:
    return invitations.list_invitations(ctx)


@router.post("/invitations/{invitation_id}/resend")
def resend_invitation(
    invitation_id: UUID,
    mail: MailSender = Depends(get_mail_sender),
    ctx: TenantContext = require("member.invitation.manage"),
) -> dict:
    return invitations.resend_invitation(ctx, invitation_id, mail)


@router.post("/invitations/{invitation_id}/revoke")
def revoke_invitation(
    invitation_id: UUID, ctx: TenantContext = require("member.invitation.manage")
) -> dict:
    return invitations.revoke_invitation(ctx, invitation_id)


@router.get("/members")
def list_members(ctx: TenantContext = require("member.read")) -> list[dict]:
    return members.list_members(ctx)


@router.patch("/members/{user_id}/grants")
def update_member_grants(
    user_id: UUID,
    body: members.GrantChangeBody,
    ctx: TenantContext = require("member.role.assign"),
) -> dict:
    return members.change_grants(ctx, user_id, body)


@router.delete("/members/{user_id}")
def remove_member(user_id: UUID, ctx: TenantContext = require("member.remove")) -> dict:
    return members.remove_member(ctx, user_id)


# Spec 12.2 omits suspension and Appendix A has no suspend key. Suspension is a reversible
# removal, so member.remove guards both routes (decided under task 1.10).
@router.post("/members/{user_id}/suspend")
def suspend_member(user_id: UUID, ctx: TenantContext = require("member.remove")) -> dict:
    return members.suspend_member(ctx, user_id)


@router.post("/members/{user_id}/reinstate")
def reinstate_member(user_id: UUID, ctx: TenantContext = require("member.remove")) -> dict:
    return members.reinstate_member(ctx, user_id)


@router.post("/ownership-transfer")
def transfer_ownership(
    body: TransferBody, ctx: TenantContext = require("tenant.ownership.transfer")
) -> dict:
    return ownership.transfer_ownership(ctx, body.new_owner_user_id)


@router.put("/execution-mode")
def set_execution_mode(
    body: mode.ModeBody, ctx: TenantContext = require("execution_mode.manage")
) -> dict:
    """Set one exact scope to SHADOW or LIVE. LIVE needs a POL-14 attestation for that scope."""
    return mode.set_mode(ctx, body)


@router.get("/execution-readiness")
def execution_readiness(ctx: TenantContext = require("execution.read")) -> list[dict]:
    """Each exact scope's mode, and whether a stored attestation passes the ten POL-14 checks,
    naming the failing one. The image digest comes from the runtime-identity service only."""
    return readiness.readiness(ctx)
