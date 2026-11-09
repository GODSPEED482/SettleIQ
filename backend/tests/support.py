"""Helpers for tests that drive the real routes as real database users."""

from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.authz.access import Principal, PrincipalKind
from app.authz.guard import TENANT_HEADER, authenticate
from app.db import global_tx, scoped_tx
from app.main import create_app
from app.tenancy.creation import create_tenant

app = create_app()


def make_user(email: str | None = None) -> UUID:
    user_id = uuid4()
    with global_tx() as conn:
        conn.execute(
            text("INSERT INTO users (id, email, email_verified_at) VALUES (:i, :e, now())"),
            {"i": user_id, "e": email or f"{user_id}@example.com"},
        )
    return user_id


class Actor:
    """One signed-in person, calling the API as themselves."""

    def __init__(self, email: str | None = None) -> None:
        self.id = make_user(email)
        self.principal = Principal(self.id, PrincipalKind.HUMAN, email_verified=True)
        self.client = TestClient(app)

    def request(self, method: str, path: str, tenant: UUID | None = None, **kwargs):
        headers = {TENANT_HEADER: str(tenant)} if tenant else {}
        app.dependency_overrides[authenticate] = lambda: self.principal
        return self.client.request(method, path, headers=headers, **kwargs)

    def create_tenant(self, name: str = "T") -> UUID:
        return create_tenant(self.principal, name, [])

    def held(self, tenant: UUID) -> set[str]:
        response = self.request("GET", "/api/me/permissions", params={"tenant_id": tenant})
        return {item["key"] for item in response.json()["permissions"]}


def add_member(tenant: UUID, user_id: UUID, status: str = "ACTIVE") -> None:
    with scoped_tx(tenant) as conn:
        conn.execute(
            text("INSERT INTO tenant_members (tenant_id, user_id, status) VALUES (:t, :u, :s)"),
            {"t": tenant, "u": user_id, "s": status},
        )


def grant_set(tenant: UUID, user_id: UUID, set_key: str) -> None:
    with scoped_tx(tenant) as conn:
        conn.execute(
            text(
                """
                INSERT INTO member_grants (id, tenant_id, user_id, permission_set_id)
                SELECT :id, :t, :u, s.id FROM permission_sets s WHERE s.key = :key
                """
            ),
            {"id": uuid4(), "t": tenant, "u": user_id, "key": set_key},
        )


def owner_of(tenant: UUID) -> UUID:
    with scoped_tx(tenant) as conn:
        return conn.execute(
            text("SELECT owner_user_id FROM tenants WHERE id = :t"), {"t": tenant}
        ).scalar_one()


def member_status(tenant: UUID, user_id: UUID) -> str | None:
    with scoped_tx(tenant) as conn:
        return conn.execute(
            text("SELECT status FROM tenant_members WHERE user_id = :u"), {"u": user_id}
        ).scalar_one_or_none()


def clear_overrides() -> None:
    app.dependency_overrides.clear()
