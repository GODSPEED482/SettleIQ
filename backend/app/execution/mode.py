"""Exact-scope SHADOW and LIVE (spec 9.5, POL-06, AC-EXEC-MODE-01..04).

An absent row is SHADOW. Switching to LIVE needs a POL-14 verdict for exactly that scope, and
the attestation ID is recorded on the row; SHADOW never needs one, so a scope can always be
returned to it, even while revocation is pending. Each switch, and each refused LIVE switch, is
audited.
"""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import Connection, text

from app.audit import write_audit
from app.authz.guard import TenantContext
from app.db import tenant_tx
from app.errors import ApiError
from app.execution.scope import ExactScope, ScopeBody, exact_scope
from app.release import verifier

Mode = Literal["SHADOW", "LIVE"]


class ModeBody(ScopeBody):
    mode: Mode


@dataclass(frozen=True)
class ScopeMode:
    mode: Mode
    attestation_id: UUID | None = None


def resolve_mode(conn: Connection, scope: ExactScope) -> ScopeMode:
    """The mode of exactly this scope. Nothing is inherited from any other row."""
    row = conn.execute(
        text(
            """
            SELECT mode, attestation_id FROM execution_mode_settings
            WHERE domain_pack_id = :pack AND connector_id = :connector
              AND action_capability = :capability
            """
        ),
        {"pack": scope.domain_pack_id, "connector": scope.connector_id,
         "capability": scope.action_capability},
    ).one_or_none()
    return ScopeMode(row.mode, row.attestation_id) if row else ScopeMode("SHADOW")


def _detail(scope: ExactScope) -> dict[str, str]:
    return {"domain_pack_id": scope.domain_pack_id,
            "domain_pack_version": scope.domain_pack_version,
            "connector_id": scope.connector_id, "action_capability": scope.action_capability}


def set_mode(ctx: TenantContext, body: ModeBody) -> dict:
    with tenant_tx(ctx) as conn:
        scope = exact_scope(conn, ctx.tenant_id, ScopeBody(**body.model_dump(exclude={"mode"})))
        verdict = verifier.verify(conn, scope) if body.mode == "LIVE" else None
        if verdict is None or verdict.allowed:
            previous = resolve_mode(conn, scope)
            attestation = verdict.attestation_id if verdict else None
            conn.execute(
                text(
                    """
                    INSERT INTO execution_mode_settings (id, tenant_id, domain_pack_id,
                        domain_pack_version, connector_id, action_capability, mode,
                        attestation_id, changed_by)
                    VALUES (:id, :tenant, :pack, :version, :connector, :capability, :mode,
                        :attestation, :user)
                    ON CONFLICT (tenant_id, domain_pack_id, connector_id, action_capability)
                    DO UPDATE SET mode = EXCLUDED.mode, attestation_id = EXCLUDED.attestation_id,
                        domain_pack_version = EXCLUDED.domain_pack_version,
                        changed_by = EXCLUDED.changed_by, changed_at = now()
                    """
                ),
                {"id": uuid4(), "tenant": ctx.tenant_id, "pack": scope.domain_pack_id,
                 "version": scope.domain_pack_version, "connector": scope.connector_id,
                 "capability": scope.action_capability, "mode": body.mode,
                 "attestation": attestation, "user": ctx.principal.id},
            )
            write_audit(
                conn, ctx.tenant_id, ctx.principal, "execution_mode.changed", "execution_mode",
                {**_detail(scope), "from": previous.mode, "to": body.mode,
                 "attestation_id": str(attestation) if attestation else None},
            )
            return {**_detail(scope), "mode": body.mode, "previous_mode": previous.mode,
                    "attestation_id": str(attestation) if attestation else None}
    # Refused: recorded in its own transaction, then reported with the failing check.
    with tenant_tx(ctx) as conn:
        write_audit(
            conn, ctx.tenant_id, ctx.principal, "execution_mode.live_denied", "execution_mode",
            {**_detail(scope), "policy": "POL-14", "check": verdict.check},
        )
    raise ApiError(403, "POLICY_DENIED", "No valid release attestation covers this exact scope.",
                   policy="POL-14", check=verdict.check)
