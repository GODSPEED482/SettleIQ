"""Audit events written inside the caller's transaction, so they commit with the change."""

import json
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, text

from app.authz.access import Principal

_INSERT_SQL = text(
    """
    INSERT INTO audit_logs (id, tenant_id, principal_kind, principal_id, invoker_user_id, action,
        resource, detail)
    VALUES (:id, :tenant_id, :kind, :principal_id, :invoker, :action, :resource,
        CAST(:detail AS jsonb))
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
) -> None:
    conn.execute(
        _INSERT_SQL,
        {
            "id": uuid4(),
            "tenant_id": tenant_id,
            "kind": principal.kind.value,
            "principal_id": principal.id,
            "invoker": invoker_user_id,
            "action": action,
            "resource": resource,
            "detail": json.dumps(detail or {}),
        },
    )
