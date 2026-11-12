"""Execution readiness (spec 9.5): each exact scope the tenant's active packs declare, and its
mode. Execution still needs LIVE, approval and current preconditions.
"""

from typing import Any

from app.authz.guard import TenantContext
from app.db import tenant_tx
from app.execution.mode import resolve_mode
from app.execution.scope import declared_scopes


def readiness(ctx: TenantContext) -> list[dict[str, Any]]:
    out = []
    with tenant_tx(ctx) as conn:
        for scope in declared_scopes(conn, ctx.tenant_id):
            out.append({
                "domain_pack_id": scope.domain_pack_id,
                "domain_pack_version": scope.domain_pack_version,
                "connector_id": scope.connector_id,
                "action_capability": scope.action_capability,
                "mode": resolve_mode(conn, scope).mode,
            })
    return out
