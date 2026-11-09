"""Atomic tenant ownership transfer (spec 3.2). The only code that changes the owner pointer."""

from uuid import UUID

from sqlalchemy import text

from app.audit import write_audit
from app.authz.guard import TenantContext
from app.db import tenant_tx
from app.errors import not_found, policy_denied, validation_failed


def transfer_ownership(ctx: TenantContext, new_owner_id: UUID) -> dict[str, str]:
    """Pointer and audit event commit together, and the tenant row lock serializes transfers.

    A caller who lost a race passed the permission check as owner, so ownership is checked again
    once the lock is held.
    """
    if new_owner_id == ctx.principal.id:
        raise validation_failed("The new owner must be a different member.")
    with tenant_tx(ctx) as conn:
        owner = conn.execute(
            text("SELECT owner_user_id FROM tenants WHERE id = :tenant FOR UPDATE"),
            {"tenant": ctx.tenant_id},
        ).scalar_one_or_none()
        if owner != ctx.principal.id:
            raise policy_denied("POL-04")
        target = conn.execute(
            text(
                """
                SELECT 1 FROM tenant_members
                WHERE tenant_id = :tenant AND user_id = :user AND status = 'ACTIVE'
                FOR UPDATE
                """
            ),
            {"tenant": ctx.tenant_id, "user": new_owner_id},
        ).first()
        if target is None:
            raise not_found()
        conn.execute(
            text("UPDATE tenants SET owner_user_id = :user, updated_at = now() WHERE id = :tenant"),
            {"tenant": ctx.tenant_id, "user": new_owner_id},
        )
        write_audit(
            conn, ctx.tenant_id, ctx.principal, "tenant.ownership.transferred", "tenant",
            {"previous_owner": str(owner), "new_owner": str(new_owner_id)},
        )
    return {"owner_user_id": str(new_owner_id)}
