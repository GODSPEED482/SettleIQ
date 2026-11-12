"""Exact-scope SHADOW and LIVE (spec 9.5, POL-06, AC-EXEC-MODE-01..04).

An absent row is SHADOW. A holder of `execution_mode.manage` switches exactly one scope at a
time, in either direction, and each switch is audited.
"""

from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

from sqlalchemy import Connection, text

from app.audit import write_audit
from app.authz.guard import TenantContext
from app.db import tenant_tx
from app.execution.scope import ExactScope, ScopeBody, exact_scope

Mode = Literal["SHADOW", "LIVE"]


class ModeBody(ScopeBody):
    mode: Mode


@dataclass(frozen=True)
class ScopeMode:
    mode: Mode


def resolve_mode(conn: Connection, scope: ExactScope) -> ScopeMode:
    """The mode of exactly this scope. Nothing is inherited from any other row."""
    row = conn.execute(
        text(
            """
            SELECT mode FROM execution_mode_settings
            WHERE domain_pack_id = :pack AND connector_id = :connector
              AND action_capability = :capability
            """
        ),
        {"pack": scope.domain_pack_id, "connector": scope.connector_id,
         "capability": scope.action_capability},
    ).one_or_none()
    return ScopeMode(row.mode) if row else ScopeMode("SHADOW")


def _detail(scope: ExactScope) -> dict[str, str]:
    return {"domain_pack_id": scope.domain_pack_id,
            "domain_pack_version": scope.domain_pack_version,
            "connector_id": scope.connector_id, "action_capability": scope.action_capability}


def set_mode(ctx: TenantContext, body: ModeBody) -> dict:
    with tenant_tx(ctx) as conn:
        scope = exact_scope(conn, ctx.tenant_id, ScopeBody(**body.model_dump(exclude={"mode"})))
        previous = resolve_mode(conn, scope)
        conn.execute(
            text(
                """
                INSERT INTO execution_mode_settings (id, tenant_id, domain_pack_id,
                    domain_pack_version, connector_id, action_capability, mode, changed_by)
                VALUES (:id, :tenant, :pack, :version, :connector, :capability, :mode, :user)
                ON CONFLICT (tenant_id, domain_pack_id, connector_id, action_capability)
                DO UPDATE SET mode = EXCLUDED.mode,
                    domain_pack_version = EXCLUDED.domain_pack_version,
                    changed_by = EXCLUDED.changed_by, changed_at = now()
                """
            ),
            {"id": uuid4(), "tenant": ctx.tenant_id, "pack": scope.domain_pack_id,
             "version": scope.domain_pack_version, "connector": scope.connector_id,
             "capability": scope.action_capability, "mode": body.mode,
             "user": ctx.principal.id},
        )
        write_audit(
            conn, ctx.tenant_id, ctx.principal, "execution_mode.changed", "execution_mode",
            {**_detail(scope), "from": previous.mode, "to": body.mode},
        )
        return {**_detail(scope), "mode": body.mode, "previous_mode": previous.mode}
