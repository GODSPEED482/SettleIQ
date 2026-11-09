"""Member listing, grant changes and removal (spec 3.1, POL-02, 04, 05, 07, 13).

POL-04 needs at least one ACTIVE human holding member.role.assign. The owner-derived set
includes it and the owner cannot be removed, so protecting the owner protects that too.
"""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy import Connection, text

from app.audit import write_audit
from app.authz.guard import TenantContext
from app.db import tenant_tx
from app.errors import not_found, policy_denied
from app.tenancy.grants import GrantInput, check_grants, install_grants


class GrantChangeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    add: list[GrantInput] = []
    revoke: list[UUID] = []

    @model_validator(mode="after")
    def _has_a_change(self) -> "GrantChangeBody":
        if not self.add and not self.revoke:
            raise ValueError("add or revoke at least one grant")
        return self


def _lock_tenant(conn: Connection, tenant_id: UUID) -> UUID:
    """Serialize membership changes, and return the current owner."""
    return conn.execute(
        text("SELECT owner_user_id FROM tenants WHERE id = :tenant FOR UPDATE"),
        {"tenant": tenant_id},
    ).scalar_one()


def _require_member(conn: Connection, tenant_id: UUID, user_id: UUID, statuses: set[str]) -> None:
    status = conn.execute(
        text(
            """
            SELECT status FROM tenant_members
            WHERE tenant_id = :tenant AND user_id = :user FOR UPDATE
            """
        ),
        {"tenant": tenant_id, "user": user_id},
    ).scalar_one_or_none()
    if status not in statuses:
        raise not_found()


def list_members(ctx: TenantContext) -> list[dict[str, Any]]:
    with tenant_tx(ctx) as conn:
        owner = conn.execute(text("SELECT owner_user_id FROM tenants")).scalar_one()
        members = conn.execute(
            text(
                """
                SELECT m.user_id, m.status, m.joined_at, u.email
                FROM tenant_members m JOIN users u ON u.id = m.user_id
                WHERE m.status <> 'REMOVED'
                ORDER BY u.email
                """
            )
        ).all()
        grants = conn.execute(
            text(
                """
                SELECT g.id, g.user_id, s.key, s.display_name, g.scope_type, g.scope_id
                FROM member_grants g JOIN permission_sets s ON s.id = g.permission_set_id
                WHERE g.user_id IS NOT NULL AND g.revoked_at IS NULL
                  AND (g.expires_at IS NULL OR g.expires_at > now())
                ORDER BY s.key
                """
            )
        ).all()
    by_user: dict[UUID, list[dict[str, Any]]] = {}
    for grant in grants:
        by_user.setdefault(grant.user_id, []).append(
            {
                "id": str(grant.id),
                "permission_set_key": grant.key,
                "display_name": grant.display_name,
                "scope_type": grant.scope_type,
                "scope_id": grant.scope_id,
            }
        )
    return [
        {
            "user_id": str(member.user_id),
            "email": member.email,
            "status": member.status,
            "joined_at": member.joined_at.isoformat() if member.joined_at else None,
            "owns_tenant": member.user_id == owner,
            "grants": by_user.get(member.user_id, []),
        }
        for member in members
    ]


def change_grants(ctx: TenantContext, user_id: UUID, body: GrantChangeBody) -> dict[str, Any]:
    if body.add and user_id == ctx.principal.id:
        raise policy_denied("POL-05")
    with tenant_tx(ctx) as conn:
        _lock_tenant(conn, ctx.tenant_id)
        _require_member(conn, ctx.tenant_id, user_id, {"ACTIVE"})
        resolved = check_grants(conn, ctx.access, body.add)
        install_grants(conn, ctx.tenant_id, user_id, ctx.principal.id, resolved)
        if body.revoke:
            revoked = conn.execute(
                text(
                    """
                    UPDATE member_grants SET revoked_at = now(), revoked_by = :by
                    WHERE id = ANY(:ids) AND user_id = :user AND revoked_at IS NULL
                    RETURNING id
                    """
                ),
                {"ids": body.revoke, "user": user_id, "by": ctx.principal.id},
            ).all()
            if len(revoked) != len(set(body.revoke)):
                raise not_found()
        write_audit(
            conn, ctx.tenant_id, ctx.principal, "member.grants.changed", "member",
            {
                "user_id": str(user_id),
                "added": [grant.permission_set_key for grant in resolved],
                "revoked": [str(grant_id) for grant_id in body.revoke],
            },
        )
    return {"user_id": str(user_id), "status": "UPDATED"}


def remove_member(ctx: TenantContext, user_id: UUID) -> dict[str, str]:
    """Self-removal is allowed, as long as the caller is not the owner (POL-05)."""
    with tenant_tx(ctx) as conn:
        owner = _lock_tenant(conn, ctx.tenant_id)
        if user_id == owner:
            raise policy_denied("POL-04")
        _require_member(conn, ctx.tenant_id, user_id, {"ACTIVE", "SUSPENDED"})
        conn.execute(
            text(
                "UPDATE tenant_members SET status = 'REMOVED' "
                "WHERE tenant_id = :tenant AND user_id = :user"
            ),
            {"tenant": ctx.tenant_id, "user": user_id},
        )
        conn.execute(
            text(
                """
                UPDATE member_grants SET revoked_at = now(), revoked_by = :by
                WHERE user_id = :user AND revoked_at IS NULL
                """
            ),
            {"user": user_id, "by": ctx.principal.id},
        )
        write_audit(
            conn, ctx.tenant_id, ctx.principal, "member.removed", "member",
            {"user_id": str(user_id)},
        )
    return {"user_id": str(user_id), "status": "REMOVED"}
