"""Audit events written inside the caller's transaction, so they commit with the change."""

import json
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, text

from app.authz.access import Principal
from app.db import tenant_tx
from app.errors import not_found

if TYPE_CHECKING:
    from app.authz.guard import TenantContext

_INSERT_SQL = text(
    """
    INSERT INTO audit_logs (id, tenant_id, principal_kind, principal_id, invoker_user_id, action,
        permission_key, resource, decision, policy_rules, detail)
    VALUES (:id, :tenant_id, :kind, :principal_id, :invoker, :action, :permission, :resource,
        :decision, :rules, CAST(:detail AS jsonb))
    """
)


def write_audit(
    conn: Connection,
    tenant_id: UUID,
    principal: Principal,
    action: str,
    resource: str,
    detail: dict[str, Any] | None = None,
    invoker_user_id: UUID | None = None,
    *,
    permission_key: str | None = None,
    decision: str | None = None,
    policy_rules: list[str] | tuple[str, ...] = (),
) -> None:
    """`permission_key`, `decision` and `policy_rules` record a policy decision with the
    permission and policy IDs it applied (AC-AUTHZ-14)."""
    conn.execute(
        _INSERT_SQL,
        {
            "id": uuid4(),
            "tenant_id": tenant_id,
            "kind": principal.kind.value,
            "principal_id": principal.id,
            "invoker": invoker_user_id,
            "action": action,
            "permission": permission_key,
            "resource": resource,
            "decision": decision,
            "rules": list(policy_rules),
            "detail": json.dumps(detail or {}),
        },
    )


def read_audit(ctx: "TenantContext", audit_id: UUID) -> dict[str, Any]:
    with tenant_tx(ctx) as conn:
        row = conn.execute(
            text(
                """
                SELECT id, principal_kind, principal_id, invoker_user_id, action, permission_key,
                    resource, scope, decision, policy_rules, detail, created_at
                FROM audit_logs WHERE id = :id
                """
            ),
            {"id": audit_id},
        ).first()
    if row is None:
        raise not_found()
    return {
        "id": str(row.id),
        "principal_kind": row.principal_kind,
        "principal_id": str(row.principal_id),
        "invoker_user_id": str(row.invoker_user_id) if row.invoker_user_id else None,
        "action": row.action,
        "permission_key": row.permission_key,
        "resource": row.resource,
        "scope": row.scope,
        "decision": row.decision,
        "policy_rules": row.policy_rules,
        "detail": row.detail,
        "created_at": row.created_at.isoformat(),
    }
