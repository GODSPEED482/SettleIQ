"""Resolves effective permissions from current memberships and grants.

Resolution happens on every decision and nothing is cached (AUTHZ-03, OPEN-21-19), so a
revoked grant stops working on the next request (AC-IAM-07).
"""

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import text

from app.authz.catalogue import Catalogue, get_catalogue
from app.db import scoped_tx

logger = logging.getLogger("settleiq.authz")


class PrincipalKind(StrEnum):
    HUMAN = "human"
    AGENT = "agent"
    WORKER = "worker"
    PLATFORM_HUMAN = "platform_human"


@dataclass(frozen=True)
class Principal:
    id: UUID
    kind: PrincipalKind
    email_verified: bool = False
    # A Worker carries the execution grant it authenticated with (POL-09); nobody else does.
    grant_id: UUID | None = None


@dataclass(frozen=True)
class Scope:
    type: str
    id: str


@dataclass(frozen=True)
class GrantRow:
    permission: str
    effect: str
    scope: Scope | None = None


@dataclass(frozen=True)
class Decision:
    tenant_id: UUID
    principal: Principal
    permission: str
    resource: str
    scope: Scope | None
    allowed: bool


class EffectiveAccess:
    """Union of grants within one tenant, where a matching DENY wins (AUTHZ-04)."""

    def __init__(self, rows: Iterable[GrantRow], catalogue: Catalogue) -> None:
        self._allow: set[tuple[str, Scope | None]] = set()
        self._deny: set[tuple[str, Scope | None]] = set()
        for row in rows:
            if row.effect == "DENY":
                self._deny.add((row.permission, row.scope))
            elif row.scope is None or row.scope.type in catalogue.get(
                row.permission
            ).narrowable_scopes:
                self._allow.add((row.permission, row.scope))
            else:
                # A scope the permission does not declare grants nothing (spec 3.3).
                logger.warning("ignored invalid scope on grant of %s", row.permission)

    def permits(self, permission: str, scope: Scope | None = None) -> bool:
        candidates = {(permission, None), (permission, scope)}
        if candidates & self._deny:
            return False
        return bool(candidates & self._allow)

    def summary(self) -> list[dict]:
        """Effective permissions for GET /api/me/permissions (spec 12.3)."""
        result = []
        for key in sorted({permission for permission, _ in self._allow}):
            if (key, None) in self._deny:
                continue
            denied = {scope for permission, scope in self._deny if permission == key}
            scopes = [
                scope
                for permission, scope in self._allow
                if permission == key and scope is not None and scope not in denied
            ]
            tenant_wide = (key, None) in self._allow
            if tenant_wide or scopes:
                result.append(
                    {
                        "key": key,
                        "tenant_wide": tenant_wide,
                        "scopes": [{"type": s.type, "id": s.id} for s in scopes],
                        "denied_scopes": [{"type": s.type, "id": s.id} for s in denied],
                    }
                )
        return result


class AccessGateway(Protocol):
    def resolve(self, principal: Principal, tenant_id: UUID) -> EffectiveAccess | None:
        """Return access for an ACTIVE member; None for a nonexistent or non-member tenant."""

    def resolve_platform(self, principal: Principal) -> EffectiveAccess | None:
        """Return platform access. No path grants this until break-glass is built."""

    def resolve_agent(
        self, agent: Principal, tenant_id: UUID, invoker_id: UUID
    ) -> EffectiveAccess | None:
        """POL-08: the Agent's set intersected with the invoker's current access."""

    def record_decision(self, decision: Decision) -> None:
        """Durably audit a sensitive or critical decision (AUTHZ-09)."""


_MEMBERSHIP_SQL = text(
    """
    SELECT t.owner_user_id
    FROM tenant_members m JOIN tenants t ON t.id = m.tenant_id
    WHERE m.tenant_id = :tenant_id AND m.user_id = :user_id AND m.status = 'ACTIVE'
    """
)

_GRANTS_SQL = text(
    """
    SELECT i.permission_key, i.effect, g.scope_type, g.scope_id
    FROM member_grants g
    JOIN permission_sets s ON s.id = g.permission_set_id AND s.status = 'ACTIVE'
    JOIN permission_set_items i ON i.permission_set_id = s.id
    WHERE g.tenant_id = :tenant_id AND g.user_id = :user_id AND g.revoked_at IS NULL
      AND (g.expires_at IS NULL OR g.expires_at > now())
    """
)

_AGENT_GRANTS_SQL = text(
    """
    SELECT i.permission_key, i.effect, g.scope_type, g.scope_id
    FROM service_principals p
    JOIN member_grants g ON g.tenant_id = p.tenant_id AND g.service_principal_id = p.id
    JOIN permission_sets s ON s.id = g.permission_set_id AND s.status = 'ACTIVE'
    JOIN permission_set_items i ON i.permission_set_id = s.id
    WHERE p.id = :agent_id AND p.kind = 'agent' AND p.status = 'ACTIVE'
      AND g.revoked_at IS NULL AND (g.expires_at IS NULL OR g.expires_at > now())
    """
)

_OWNER_ITEMS_SQL = text(
    """
    SELECT i.permission_key, i.effect, NULL AS scope_type, NULL AS scope_id
    FROM permission_sets s
    JOIN permission_set_items i ON i.permission_set_id = s.id
    WHERE s.derivation = 'OWNER_POINTER' AND s.status = 'ACTIVE'
    """
)

_WORKER_GRANT_SQL = text(
    """
    SELECT g.permission_keys
    FROM execution_grants g
    JOIN service_principals p ON p.tenant_id = g.tenant_id AND p.id = :worker_id
    WHERE g.id = :grant_id AND p.kind = 'worker' AND p.status = 'ACTIVE'
      AND g.consumed_at IS NULL AND g.expires_at > now()
    """
)

_AUDIT_SQL = text(
    """
    INSERT INTO audit_logs (id, tenant_id, principal_kind, principal_id, action,
        permission_key, resource, scope, decision)
    VALUES (:id, :tenant_id, :kind, :principal_id, 'authz.decision', :permission, :resource,
        CAST(:scope AS jsonb), :decision)
    """
)


def _to_row(raw) -> GrantRow:
    scope = Scope(raw.scope_type, raw.scope_id) if raw.scope_type else None
    return GrantRow(raw.permission_key, raw.effect, scope)


class PostgresAccessGateway:
    def __init__(self, catalogue: Catalogue) -> None:
        self._catalogue = catalogue

    def resolve(self, principal: Principal, tenant_id: UUID) -> EffectiveAccess | None:
        # The Agent resolves only through resolve_agent, on behalf of one invoker.
        if principal.kind is PrincipalKind.WORKER:
            return self._resolve_worker(principal, tenant_id)
        if principal.kind is not PrincipalKind.HUMAN:
            return None
        params = {"tenant_id": tenant_id, "user_id": principal.id}
        # The selector is only used to scope this read; a tenant the caller is not an
        # ACTIVE member of returns no row, and access is refused.
        with scoped_tx(tenant_id) as conn:
            membership = conn.execute(_MEMBERSHIP_SQL, params).first()
            if membership is None:
                return None
            rows = list(conn.execute(_GRANTS_SQL, params))
            if membership.owner_user_id == principal.id:
                rows.extend(conn.execute(_OWNER_ITEMS_SQL))
        return EffectiveAccess((_to_row(row) for row in rows), self._catalogue)

    def _resolve_worker(self, worker: Principal, tenant_id: UUID) -> EffectiveAccess | None:
        """POL-09: the Worker holds exactly its grant's keys, and only while that grant is
        unconsumed and unexpired. Worker-only keys come from nowhere else (AC-AUTHZ-06)."""
        if worker.grant_id is None:
            return None
        with scoped_tx(tenant_id) as conn:
            keys = conn.execute(
                _WORKER_GRANT_SQL, {"grant_id": worker.grant_id, "worker_id": worker.id}
            ).scalar_one_or_none()
        if not keys:
            return None
        return EffectiveAccess((GrantRow(key, "ALLOW") for key in keys), self._catalogue)

    def resolve_platform(self, principal: Principal) -> EffectiveAccess | None:
        return None

    def resolve_agent(
        self, agent: Principal, tenant_id: UUID, invoker_id: UUID
    ) -> EffectiveAccess | None:
        """POL-08, resolved afresh on every tool call. A key a human may also hold survives only
        if the invoker holds it tenant-wide right now; agent-only keys remain. A DENY on either
        side wins, and an invoker who is no longer an ACTIVE member leaves the Agent nothing.
        """
        if agent.kind is not PrincipalKind.AGENT:
            return None
        invoker = self.resolve(Principal(invoker_id, PrincipalKind.HUMAN, True), tenant_id)
        if invoker is None:
            return None
        with scoped_tx(tenant_id) as conn:
            rows = [_to_row(row) for row in conn.execute(_AGENT_GRANTS_SQL,
                                                         {"agent_id": agent.id})]
        if not rows:
            return None
        kept = []
        for row in rows:
            spec = self._catalogue.get(row.permission)
            human_data = "human" in spec.principal_types
            if row.effect == "DENY" or not human_data or invoker.permits(row.permission):
                kept.append(row)
            if human_data and not invoker.permits(row.permission):
                kept.append(GrantRow(row.permission, "DENY"))
        return EffectiveAccess(kept, self._catalogue)

    def record_decision(self, decision: Decision) -> None:
        scope = (
            json.dumps({"type": decision.scope.type, "id": decision.scope.id})
            if decision.scope
            else None
        )
        with scoped_tx(decision.tenant_id) as conn:
            conn.execute(
                _AUDIT_SQL,
                {
                    "id": uuid4(),
                    "tenant_id": decision.tenant_id,
                    "kind": decision.principal.kind.value,
                    "principal_id": decision.principal.id,
                    "permission": decision.permission,
                    "resource": decision.resource,
                    "scope": scope,
                    "decision": "ALLOW" if decision.allowed else "DENY",
                },
            )


def get_gateway() -> AccessGateway:
    return PostgresAccessGateway(get_catalogue())
