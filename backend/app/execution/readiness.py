"""Execution readiness (spec 9.5, 9.7.6): each exact scope's mode and release-gate result.

The release gate is checked here exactly as at a LIVE switch or a grant, so a failing check is
named before anyone tries. Execution still needs LIVE, approval and current preconditions.
"""

from typing import Any

from app.authz.guard import TenantContext
from app.db import tenant_tx
from app.execution.mode import resolve_mode
from app.execution.scope import declared_scopes
from app.release import runtime_identity, verifier


def readiness(ctx: TenantContext) -> list[dict[str, Any]]:
    digest = runtime_identity.running_digest()
    out = []
    with tenant_tx(ctx) as conn:
        for scope in declared_scopes(conn, ctx.tenant_id):
            mode = resolve_mode(conn, scope)
            verdict = verifier.verify(conn, scope, digest=lambda: digest)
            out.append({
                "domain_pack_id": scope.domain_pack_id,
                "domain_pack_version": scope.domain_pack_version,
                "connector_id": scope.connector_id,
                "action_capability": scope.action_capability,
                "mode": mode.mode,
                "mode_attestation_id": str(mode.attestation_id) if mode.attestation_id else None,
                "release_gate": {
                    "passes": verdict.allowed,
                    "failing_check": verdict.check,
                    "attestation_id": (str(verdict.attestation_id)
                                       if verdict.attestation_id else None),
                },
            })
    return out
