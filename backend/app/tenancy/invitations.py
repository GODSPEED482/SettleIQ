"""Tenant invitations (spec 3.1, POL-02, POL-13, AC-IAM-02 to 10).

Tokens are stored only as hashes and resending rotates them, so earlier links stop resolving.
Email is sent after the commit and its outcome recorded separately, so a failed send stays
visible and retryable and can never activate anyone.
"""

import logging
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import Connection, text

from app.audit import write_audit
from app.auth.accounts import normalize_email
from app.auth.tokens import hash_token, new_token
from app.authz.access import AccessGateway, Principal, PrincipalKind
from app.authz.guard import TenantContext
from app.config import get_settings
from app.db import global_tx, scoped_tx, tenant_tx
from app.errors import ApiError, conflict, not_found
from app.mail import MailDeliveryError, MailSender
from app.tenancy.grants import GrantInput, ResolvedGrant, check_grants, install_grants

logger = logging.getLogger("settleiq.invitations")


class InviteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    grants: list[GrantInput] = Field(min_length=1)


def _ttl() -> timedelta:
    return timedelta(hours=get_settings().invitation_ttl_hours)


def _deliver(
    tenant_id: UUID, invitation_id: UUID, email: str, raw: str, token_hash: str, mail: MailSender
) -> str:
    link = f"{get_settings().app_base_url}/invitations/accept#token={raw}"
    status, error = "SENT", None
    try:
        mail.send(
            email, "You have been invited to SettleIQ", f"Accept your invitation:\n\n{link}\n"
        )
    except MailDeliveryError as failure:
        logger.warning("invitation email not delivered: %s", failure)
        status, error = "FAILED", str(failure)
    with scoped_tx(tenant_id) as conn:
        # Matched on the hash, so a slow send cannot overwrite the state of a newer resend.
        conn.execute(
            text(
                """
                UPDATE tenant_invitations
                SET delivery_status = :status, delivery_attempts = delivery_attempts + 1,
                    last_delivery_error = :error,
                    last_sent_at = CASE WHEN :status = 'SENT' THEN now() ELSE last_sent_at END
                WHERE id = :id AND token_hash = :hash
                """
            ),
            {"status": status, "error": error, "id": invitation_id, "hash": token_hash},
        )
    return status


def _insert_invitation(
    conn: Connection, ctx: TenantContext, email: str, resolved: list[ResolvedGrant]
) -> tuple[UUID, str, str]:
    invitation_id = uuid4()
    raw, token_hash = new_token()
    conn.execute(
        text(
            """
            INSERT INTO tenant_invitations
                (id, tenant_id, email, token_hash, status, expires_at, invited_by)
            VALUES (:id, :tenant, :email, :hash, 'PENDING', now() + :ttl, :by)
            """
        ),
        {
            "id": invitation_id,
            "tenant": ctx.tenant_id,
            "email": email,
            "hash": token_hash,
            "ttl": _ttl(),
            "by": ctx.principal.id,
        },
    )
    for grant in resolved:
        conn.execute(
            text(
                """
                INSERT INTO tenant_invitation_grants
                    (id, tenant_id, invitation_id, permission_set_id, scope_type, scope_id)
                VALUES (:id, :tenant, :invitation, :set_id, :scope_type, :scope_id)
                """
            ),
            {
                "id": uuid4(),
                "tenant": ctx.tenant_id,
                "invitation": invitation_id,
                "set_id": grant.permission_set_id,
                "scope_type": grant.scope_type,
                "scope_id": grant.scope_id,
            },
        )
    return invitation_id, raw, token_hash


def _refuse_duplicates(conn: Connection, email: str) -> None:
    active = conn.execute(
        text(
            """
            SELECT 1 FROM tenant_members m JOIN users u ON u.id = m.user_id
            WHERE u.email = :email AND m.status = 'ACTIVE'
            """
        ),
        {"email": email},
    ).first()
    if active is not None:
        raise conflict("ALREADY_MEMBER", "This person is already a member.")
    conn.execute(
        text(
            "UPDATE tenant_invitations SET status = 'EXPIRED' "
            "WHERE email = :email AND status = 'PENDING' AND expires_at <= now()"
        ),
        {"email": email},
    )
    pending = conn.execute(
        text("SELECT 1 FROM tenant_invitations WHERE email = :email AND status = 'PENDING'"),
        {"email": email},
    ).first()
    if pending is not None:
        raise conflict("INVITATION_EXISTS", "A pending invitation exists. Resend it instead.")


def create_invitation(ctx: TenantContext, body: InviteBody, mail: MailSender) -> dict[str, str]:
    email = normalize_email(body.email)
    with tenant_tx(ctx) as conn:
        conn.execute(
            text("SELECT id FROM tenants WHERE id = :t FOR UPDATE"), {"t": ctx.tenant_id}
        )
        resolved = check_grants(conn, ctx.access, body.grants)
        _refuse_duplicates(conn, email)
        invitation_id, raw, token_hash = _insert_invitation(conn, ctx, email, resolved)
        write_audit(
            conn, ctx.tenant_id, ctx.principal, "invitation.created", "invitation",
            {
                "invitation_id": str(invitation_id),
                "email": email,
                "sets": [grant.permission_set_key for grant in resolved],
            },
        )
    status = _deliver(ctx.tenant_id, invitation_id, email, raw, token_hash, mail)
    return {"id": str(invitation_id), "status": "PENDING", "delivery_status": status}


def list_invitations(ctx: TenantContext) -> list[dict[str, Any]]:
    with tenant_tx(ctx) as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, email, expires_at, delivery_status, delivery_attempts,
                    last_delivery_error, last_sent_at, created_at,
                    CASE WHEN status = 'PENDING' AND expires_at <= now()
                         THEN 'EXPIRED' ELSE status END AS current_status
                FROM tenant_invitations ORDER BY created_at DESC
                """
            )
        ).all()
    return [
        {
            "id": str(row.id),
            "email": row.email,
            "status": row.current_status,
            "expires_at": row.expires_at.isoformat(),
            "delivery_status": row.delivery_status,
            "delivery_attempts": row.delivery_attempts,
            "last_delivery_error": row.last_delivery_error,
            "last_sent_at": row.last_sent_at.isoformat() if row.last_sent_at else None,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


def _lock_pending(conn: Connection, invitation_id: UUID) -> Any:
    row = conn.execute(
        text("SELECT id, email, status FROM tenant_invitations WHERE id = :id FOR UPDATE"),
        {"id": invitation_id},
    ).first()
    if row is None:
        raise not_found()
    if row.status != "PENDING":
        raise conflict("INVITATION_NOT_PENDING", "This invitation is no longer pending.")
    return row


def resend_invitation(
    ctx: TenantContext, invitation_id: UUID, mail: MailSender
) -> dict[str, str]:
    raw, token_hash = new_token()
    with tenant_tx(ctx) as conn:
        row = _lock_pending(conn, invitation_id)
        conn.execute(
            text(
                """
                UPDATE tenant_invitations
                SET token_hash = :hash, expires_at = now() + :ttl, delivery_status = 'PENDING',
                    last_delivery_error = NULL
                WHERE id = :id
                """
            ),
            {"hash": token_hash, "ttl": _ttl(), "id": invitation_id},
        )
        write_audit(
            conn, ctx.tenant_id, ctx.principal, "invitation.resent", "invitation",
            {"invitation_id": str(invitation_id)},
        )
    status = _deliver(ctx.tenant_id, invitation_id, row.email, raw, token_hash, mail)
    return {"id": str(invitation_id), "status": "PENDING", "delivery_status": status}


def revoke_invitation(ctx: TenantContext, invitation_id: UUID) -> dict[str, str]:
    with tenant_tx(ctx) as conn:
        _lock_pending(conn, invitation_id)
        conn.execute(
            text(
                "UPDATE tenant_invitations SET status = 'REVOKED', revoked_at = now() "
                "WHERE id = :id"
            ),
            {"id": invitation_id},
        )
        write_audit(
            conn, ctx.tenant_id, ctx.principal, "invitation.revoked", "invitation",
            {"invitation_id": str(invitation_id)},
        )
    return {"id": str(invitation_id), "status": "REVOKED"}


def _recheck_grants(
    conn: Connection, tenant_id: UUID, invitation: Any, gateway: AccessGateway
) -> list[ResolvedGrant]:
    """POL-02 again at acceptance: the inviter may have lost the right to grant these."""
    inviter = gateway.resolve(
        Principal(invitation.invited_by, PrincipalKind.HUMAN, email_verified=True), tenant_id
    )
    if inviter is None:
        raise not_found()
    rows = conn.execute(
        text(
            """
            SELECT s.key, g.scope_type, g.scope_id
            FROM tenant_invitation_grants g JOIN permission_sets s ON s.id = g.permission_set_id
            WHERE g.invitation_id = :id
            """
        ),
        {"id": invitation.id},
    ).all()
    grants = [
        GrantInput(permission_set_key=row.key, scope_type=row.scope_type, scope_id=row.scope_id)
        for row in rows
    ]
    try:
        return check_grants(conn, inviter, grants)
    except ApiError:
        raise not_found() from None


def _consume(
    conn: Connection,
    tenant_id: UUID,
    invitation: Any,
    principal: Principal,
    gateway: AccessGateway,
) -> None:
    user = conn.execute(
        text("SELECT email, email_verified_at FROM users WHERE id = :id"), {"id": principal.id}
    ).first()
    if user is None or user.email_verified_at is None or user.email != invitation.email:
        raise not_found()
    resolved = _recheck_grants(conn, tenant_id, invitation, gateway)
    conn.execute(
        text(
            """
            INSERT INTO tenant_members (tenant_id, user_id, status, joined_at)
            VALUES (:tenant, :user, 'ACTIVE', now())
            ON CONFLICT (tenant_id, user_id) DO UPDATE SET status = 'ACTIVE', joined_at = now()
            """
        ),
        {"tenant": tenant_id, "user": principal.id},
    )
    install_grants(conn, tenant_id, principal.id, invitation.invited_by, resolved)
    conn.execute(
        text(
            "UPDATE tenant_invitations SET status = 'ACCEPTED', accepted_at = now() "
            "WHERE id = :id"
        ),
        {"id": invitation.id},
    )
    write_audit(
        conn, tenant_id, principal, "invitation.accepted", "invitation",
        {
            "invitation_id": str(invitation.id),
            "sets": [grant.permission_set_key for grant in resolved],
        },
    )


def accept_invitation(principal: Principal, raw: str, gateway: AccessGateway) -> dict[str, str]:
    """Every way this can fail looks the same to the caller (AC-IAM-05, spec 11.4)."""
    token_hash = hash_token(raw)
    with global_tx() as conn:
        tenant_id = conn.execute(
            text("SELECT invitation_tenant_by_token_hash(:hash)"), {"hash": token_hash}
        ).scalar_one()
    if tenant_id is None:
        raise not_found()
    expired = False
    with scoped_tx(tenant_id) as conn:
        invitation = conn.execute(
            text(
                """
                SELECT id, email, status, invited_by, expires_at <= now() AS is_expired
                FROM tenant_invitations WHERE token_hash = :hash FOR UPDATE
                """
            ),
            {"hash": token_hash},
        ).first()
        if invitation is None or invitation.status != "PENDING":
            raise not_found()
        if invitation.is_expired:
            # Recorded, so raised only after this transaction commits.
            conn.execute(
                text("UPDATE tenant_invitations SET status = 'EXPIRED' WHERE id = :id"),
                {"id": invitation.id},
            )
            expired = True
        else:
            _consume(conn, tenant_id, invitation, principal, gateway)
    if expired:
        raise not_found()
    return {"tenant_id": str(tenant_id), "status": "ACCEPTED"}
