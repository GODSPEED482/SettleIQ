"""The tenant's Worker principal and how the Worker app authenticates it (POL-09, task 5.6).

The Worker never holds a session. It authenticates only by presenting the single-use grant the
gateway issued for one execution: the bearer token is hashed and looked up in the selected
tenant, and the grant must name the execution in the path. The resulting principal carries the
grant's ID, and the access gateway gives it the grant's keys only while that grant is unconsumed
and unexpired. A human session cookie is never read on the Worker app (AC-AUTHZ-17).
"""

import hashlib
from uuid import UUID, uuid4

from fastapi import Request
from sqlalchemy import Connection, text

from app.authz.access import Principal, PrincipalKind
from app.db import scoped_tx
from app.errors import unauthenticated

TENANT_HEADER = "X-Tenant-Id"


def worker_principal(conn: Connection, tenant_id: UUID) -> Principal:
    """The tenant's Worker service principal, registered on first use.

    Work is attributed to this durable identity, never to an invented one. It holds no grants
    of its own; authority comes only from an execution grant."""
    conn.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"worker_principal:{tenant_id}"},
    )
    row = conn.execute(
        text(
            """
            SELECT id FROM service_principals
            WHERE tenant_id = :tenant AND kind = 'worker' AND status = 'ACTIVE'
            ORDER BY created_at, id
            LIMIT 1
            """
        ),
        {"tenant": tenant_id},
    ).first()
    worker_id = row.id if row is not None else uuid4()
    if row is None:
        conn.execute(
            text(
                "INSERT INTO service_principals (id, tenant_id, kind, status) "
                "VALUES (:id, :tenant, 'worker', 'ACTIVE')"
            ),
            {"id": worker_id, "tenant": tenant_id},
        )
    return Principal(worker_id, PrincipalKind.WORKER, email_verified=True)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def grant_principal(request: Request) -> Principal:
    """The Worker named by a bearer grant token for the execution in the path. Anything else,
    including a session cookie alone, is unauthenticated."""
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise unauthenticated()
    try:
        tenant_id = UUID(request.headers.get(TENANT_HEADER, ""))
        execution_id = UUID(request.path_params.get("execution_id", ""))
    except ValueError:
        raise unauthenticated() from None
    with scoped_tx(tenant_id) as conn:
        grant = conn.execute(
            text("SELECT id, execution_id FROM execution_grants WHERE token_hash = :hash"),
            {"hash": token_hash(token.strip())},
        ).one_or_none()
        if grant is None or grant.execution_id != execution_id:
            raise unauthenticated()
        worker = worker_principal(conn, tenant_id)
    return Principal(worker.id, PrincipalKind.WORKER, email_verified=True, grant_id=grant.id)
