"""Grant policy shared by invitations and member changes (POL-02, 03, 05, 07, 13)."""

from dataclasses import dataclass
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import Connection, text

from app.authz.access import EffectiveAccess
from app.authz.catalogue import get_catalogue
from app.errors import policy_denied, validation_failed


class GrantInput(BaseModel):
    """Conditions such as max_amount are refused until something enforces them (OPEN-21-17)."""

    model_config = ConfigDict(extra="forbid")

    permission_set_key: str = Field(min_length=1, max_length=100)
    scope_type: str | None = None
    scope_id: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _scope_is_all_or_nothing(self) -> "GrantInput":
        if (self.scope_type is None) != (self.scope_id is None):
            raise ValueError("scope_type and scope_id must be given together")
        return self


@dataclass(frozen=True)
class ResolvedGrant:
    permission_set_id: UUID
    permission_set_key: str
    scope_type: str | None
    scope_id: str | None


def _check_one(conn: Connection, grantor: EffectiveAccess, grant: GrantInput) -> ResolvedGrant:
    permission_set = conn.execute(
        text(
            """
            SELECT id, principal_type, derivation FROM permission_sets
            WHERE key = :key AND status = 'ACTIVE'
            ORDER BY version DESC LIMIT 1
            """
        ),
        {"key": grant.permission_set_key},
    ).first()
    if permission_set is None:
        raise validation_failed(f"Unknown permission set: {grant.permission_set_key}")
    if permission_set.derivation is not None:
        raise policy_denied("POL-13")
    if permission_set.principal_type != "human":
        raise policy_denied("POL-03")
    keys = conn.execute(
        text(
            "SELECT permission_key FROM permission_set_items "
            "WHERE permission_set_id = :id AND effect = 'ALLOW'"
        ),
        {"id": permission_set.id},
    ).scalars()
    catalogue = get_catalogue()
    for key in keys:
        spec = catalogue.get(key)
        if not spec.tenant_grantable or not all(grantor.permits(r) for r in spec.grant_requires):
            raise policy_denied("POL-02")
        # Spec 3.3: a scope a permission does not declare is rejected, never silently dropped.
        if grant.scope_type is not None and grant.scope_type not in spec.narrowable_scopes:
            raise validation_failed(f"{key} cannot be narrowed by {grant.scope_type}")
    return ResolvedGrant(
        permission_set.id, grant.permission_set_key, grant.scope_type, grant.scope_id
    )


def check_grants(
    conn: Connection, grantor: EffectiveAccess, grants: list[GrantInput]
) -> list[ResolvedGrant]:
    return [_check_one(conn, grantor, grant) for grant in grants]


def install_grants(
    conn: Connection,
    tenant_id: UUID,
    user_id: UUID,
    granted_by: UUID,
    grants: list[ResolvedGrant],
) -> None:
    """Add each grant unless the member already holds exactly that one."""
    for grant in grants:
        conn.execute(
            text(
                """
                INSERT INTO member_grants
                    (id, tenant_id, user_id, permission_set_id, scope_type, scope_id, granted_by)
                SELECT :id, :tenant, :user, :set_id, :scope_type, :scope_id, :by
                WHERE NOT EXISTS (
                    SELECT 1 FROM member_grants
                    WHERE user_id = :user AND permission_set_id = :set_id
                      AND scope_type IS NOT DISTINCT FROM :scope_type
                      AND scope_id IS NOT DISTINCT FROM :scope_id
                      AND revoked_at IS NULL
                )
                """
            ),
            {
                "id": uuid4(),
                "tenant": tenant_id,
                "user": user_id,
                "set_id": grant.permission_set_id,
                "scope_type": grant.scope_type,
                "scope_id": grant.scope_id,
                "by": granted_by,
            },
        )
