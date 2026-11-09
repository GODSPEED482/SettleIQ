"""Atomic tenant creation (spec 3.1, AC-IAM-01)."""

from uuid import UUID, uuid4

from sqlalchemy import Connection, text

from app.audit import write_audit
from app.authz.access import Principal
from app.db import scoped_tx


def provision_service_principals(conn: Connection, tenant_id: UUID) -> None:
    """Create the tenant's Agent and Worker identities.

    The Agent holds its standing set, found by principal type. The Worker gets no grant: its
    authority comes only from a single-use execution grant (spec 3.5).
    """
    agent_id, worker_id = uuid4(), uuid4()
    conn.execute(
        text(
            """
            INSERT INTO service_principals (id, tenant_id, kind, status)
            VALUES (:agent, :tenant, 'agent', 'ACTIVE'), (:worker, :tenant, 'worker', 'ACTIVE')
            """
        ),
        {"agent": agent_id, "worker": worker_id, "tenant": tenant_id},
    )
    granted = conn.execute(
        text(
            """
            INSERT INTO member_grants (id, tenant_id, service_principal_id, permission_set_id)
            SELECT :id, :tenant, :agent, s.id
            FROM permission_sets s
            WHERE s.principal_type = 'agent' AND s.status = 'ACTIVE'
            ORDER BY s.version DESC
            LIMIT 1
            """
        ),
        {"id": uuid4(), "tenant": tenant_id, "agent": agent_id},
    )
    if granted.rowcount != 1:
        raise RuntimeError("no active agent permission set; run the catalogue seed")


def create_tenant(principal: Principal, name: str, industry_labels: list[str]) -> UUID:
    """The tenant, its owner's ACTIVE membership and the owner pointer commit together.

    The owner's permissions derive from the pointer and are never stored as a grant (POL-13).
    """
    tenant_id = uuid4()
    with scoped_tx(tenant_id) as conn:
        conn.execute(
            text(
                """
                INSERT INTO tenants (id, name, industry_labels, owner_user_id)
                VALUES (:id, :name, :labels, :owner)
                """
            ),
            {"id": tenant_id, "name": name, "labels": industry_labels, "owner": principal.id},
        )
        conn.execute(
            text(
                """
                INSERT INTO tenant_members (tenant_id, user_id, status, joined_at)
                VALUES (:tenant, :user, 'ACTIVE', now())
                """
            ),
            {"tenant": tenant_id, "user": principal.id},
        )
        provision_service_principals(conn, tenant_id)
        write_audit(conn, tenant_id, principal, "tenant.created", "tenant", {"name": name})
    return tenant_id
