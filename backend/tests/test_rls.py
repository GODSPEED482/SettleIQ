"""Tenant isolation, owner invariant and permission resolution against real PostgreSQL.

These run as the runtime role, so RLS applies exactly as it does in production.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.authz.access import Decision, PostgresAccessGateway, Principal, PrincipalKind
from app.authz.catalogue import get_catalogue
from app.db import get_engine, global_tx, scoped_tx

pytestmark = pytest.mark.db

# These tests exercise the database layer only; test_iam_acceptance.py traces AC-IAM-01..11 and
# AC-TEN-01..03 through the routes.


def make_user() -> UUID:
    user_id = uuid4()
    with global_tx() as conn:
        conn.execute(
            text("INSERT INTO users (id, email, email_verified_at) VALUES (:id, :email, now())"),
            {"id": user_id, "email": f"{user_id}@example.test"},
        )
    return user_id


def make_tenant(owner: UUID) -> UUID:
    tenant_id = uuid4()
    with scoped_tx(tenant_id) as conn:
        conn.execute(
            text("INSERT INTO tenants (id, name, owner_user_id) VALUES (:id, 'T', :owner)"),
            {"id": tenant_id, "owner": owner},
        )
        add_member_row(conn, tenant_id, owner)
    return tenant_id


def add_member_row(conn, tenant_id: UUID, user_id: UUID, status: str = "ACTIVE") -> None:
    conn.execute(
        text("INSERT INTO tenant_members (tenant_id, user_id, status) VALUES (:t, :u, :s)"),
        {"t": tenant_id, "u": user_id, "s": status},
    )


def add_member(tenant_id: UUID, user_id: UUID, status: str = "ACTIVE") -> None:
    with scoped_tx(tenant_id) as conn:
        add_member_row(conn, tenant_id, user_id, status)


def grant(tenant_id: UUID, user_id: UUID, set_key: str, expires_at=None) -> UUID:
    grant_id = uuid4()
    with scoped_tx(tenant_id) as conn:
        conn.execute(
            text(
                """
                INSERT INTO member_grants (id, tenant_id, user_id, permission_set_id, expires_at)
                SELECT :id, :t, :u, s.id, :expires FROM permission_sets s WHERE s.key = :key
                """
            ),
            {"id": grant_id, "t": tenant_id, "u": user_id, "key": set_key, "expires": expires_at},
        )
    return grant_id


def set_owner(tenant_id: UUID, user_id: UUID) -> None:
    with scoped_tx(tenant_id) as conn:
        conn.execute(
            text("UPDATE tenants SET owner_user_id = :u WHERE id = :t"),
            {"u": user_id, "t": tenant_id},
        )


def set_status(tenant_id: UUID, user_id: UUID, status: str) -> None:
    with scoped_tx(tenant_id) as conn:
        conn.execute(
            text("UPDATE tenant_members SET status = :s WHERE tenant_id = :t AND user_id = :u"),
            {"s": status, "t": tenant_id, "u": user_id},
        )


def human(user_id: UUID) -> Principal:
    return Principal(user_id, PrincipalKind.HUMAN, email_verified=True)


@pytest.fixture
def gateway() -> PostgresAccessGateway:
    return PostgresAccessGateway(get_catalogue())


@pytest.fixture
def owner() -> UUID:
    return make_user()


@pytest.fixture
def tenant(owner: UUID) -> UUID:
    return make_tenant(owner)


# RLS baseline


def test_runtime_role_cannot_bypass_rls():
    with global_tx() as conn:
        row = conn.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        ).one()
    assert (row.rolsuper, row.rolbypassrls) == (False, False)


def test_every_tenant_owned_table_has_forced_rls():
    with global_tx() as conn:
        rows = conn.execute(
            text(
                """
                SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
                    (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid) AS policies
                FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relkind = 'r' AND (
                    c.relname = 'tenants' OR EXISTS (
                        SELECT 1 FROM pg_attribute a
                        WHERE a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped
                    ))
                """
            )
        ).all()
    assert len(rows) >= 7
    assert [r.relname for r in rows if not (r.relrowsecurity and r.relforcerowsecurity)] == []
    assert [r.relname for r in rows if r.policies == 0] == []


def test_every_tenant_id_column_is_not_null():
    with global_tx() as conn:
        nullable = conn.execute(
            text(
                """
                SELECT table_name FROM information_schema.columns
                WHERE table_schema = 'public' AND column_name = 'tenant_id'
                  AND is_nullable = 'YES'
                """
            )
        ).all()
    assert nullable == []


def test_no_context_sees_nothing(tenant: UUID):
    with global_tx() as conn:
        assert conn.execute(text("SELECT count(*) FROM tenants")).scalar() == 0
        assert conn.execute(text("SELECT count(*) FROM tenant_members")).scalar() == 0


def test_tenant_a_cannot_read_tenant_b(owner: UUID, tenant: UUID):
    other = make_tenant(make_user())
    with scoped_tx(tenant) as conn:
        assert conn.execute(text("SELECT id FROM tenants")).scalars().all() == [tenant]
        found = conn.execute(
            text("SELECT count(*) FROM tenant_members WHERE tenant_id = :t"), {"t": other}
        ).scalar()
    assert found == 0


def test_tenant_a_cannot_update_tenant_b(tenant: UUID):
    other = make_tenant(make_user())
    with scoped_tx(tenant) as conn:
        result = conn.execute(text("UPDATE tenants SET name = 'x' WHERE id = :t"), {"t": other})
    assert result.rowcount == 0


def test_write_into_another_tenant_is_refused(tenant: UUID):
    other = make_tenant(make_user())
    with pytest.raises(DBAPIError, match="row-level security"):
        with scoped_tx(tenant) as conn:
            add_member_row(conn, other, make_user())


def test_tenant_context_does_not_outlive_the_transaction(tenant: UUID):
    with get_engine().connect() as conn:
        with conn.begin():
            conn.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant)}
            )
            assert conn.execute(text("SELECT count(*) FROM tenants")).scalar() == 1
        with conn.begin():
            assert conn.execute(text("SELECT count(*) FROM tenants")).scalar() == 0


def test_audit_log_is_append_only(owner: UUID, tenant: UUID, gateway: PostgresAccessGateway):
    gateway.record_decision(
        Decision(tenant, human(owner), "ontology.publish", "ontology", None, True)
    )
    for statement in ("UPDATE audit_logs SET decision = 'DENY'", "DELETE FROM audit_logs"):
        with pytest.raises(DBAPIError, match="permission denied"):
            with scoped_tx(tenant) as conn:
                conn.execute(text(statement))


def test_decision_is_audited_under_its_tenant(owner, tenant, gateway):
    gateway.record_decision(
        Decision(tenant, human(owner), "ontology.publish", "ontology", None, False)
    )
    with scoped_tx(tenant) as conn:
        row = conn.execute(text("SELECT permission_key, decision FROM audit_logs")).one()
    assert tuple(row) == ("ontology.publish", "DENY")


def test_runtime_role_cannot_modify_the_catalogue():
    with pytest.raises(DBAPIError, match="permission denied"):
        with global_tx() as conn:
            conn.execute(text("UPDATE permissions SET sensitivity = 'normal'"))


def test_catalogue_is_seeded_without_identities():
    expected = sum(1 for _ in get_catalogue())
    with global_tx() as conn:
        assert conn.execute(text("SELECT count(*) FROM permissions")).scalar() == expected
        assert conn.execute(text("SELECT count(*) FROM permission_sets")).scalar() == 9


# Owner invariant. Concurrent transfers: test_ownership.py, through the route.


def test_tenant_needs_its_owner_as_member_at_commit():
    tenant_id = uuid4()
    with pytest.raises(IntegrityError):
        with scoped_tx(tenant_id) as conn:
            conn.execute(
                text("INSERT INTO tenants (id, name, owner_user_id) VALUES (:id, 'T', :o)"),
                {"id": tenant_id, "o": make_user()},
            )


def test_owner_must_be_a_member_of_the_same_tenant(tenant: UUID, owner: UUID):
    outsider = make_user()
    make_tenant(outsider)
    with pytest.raises(IntegrityError):
        set_owner(tenant, outsider)


def test_owner_cannot_transfer_to_non_active_member(tenant: UUID):
    suspended = make_user()
    add_member(tenant, suspended, "SUSPENDED")
    with pytest.raises(IntegrityError, match="ACTIVE member"):
        set_owner(tenant, suspended)


def test_current_owner_cannot_be_suspended(tenant: UUID, owner: UUID):
    with pytest.raises(IntegrityError, match="before transfer"):
        set_status(tenant, owner, "SUSPENDED")


def test_former_owner_can_be_suspended_after_transfer(tenant: UUID, owner: UUID):
    successor = make_user()
    add_member(tenant, successor)
    set_owner(tenant, successor)
    set_status(tenant, owner, "SUSPENDED")


# Permission resolution


def test_owner_gets_derived_permissions_but_no_action_rights(tenant, owner, gateway):
    access = gateway.resolve(human(owner), tenant)
    assert access.permits("tenant.ownership.transfer")
    assert access.permits("member.invite")
    assert not access.permits("playbook.request")
    assert not access.permits("playbook.approve")
    assert not access.permits("source.credential.manage")


def test_owner_permissions_follow_the_pointer_immediately(tenant, owner, gateway):
    successor = make_user()
    add_member(tenant, successor)
    set_owner(tenant, successor)
    assert not gateway.resolve(human(owner), tenant).permits("tenant.ownership.transfer")
    assert gateway.resolve(human(successor), tenant).permits("tenant.ownership.transfer")


def test_non_member_and_nonexistent_tenant_both_resolve_to_none(tenant, gateway):
    assert gateway.resolve(human(make_user()), tenant) is None
    assert gateway.resolve(human(make_user()), uuid4()) is None


def test_suspended_member_loses_access_on_next_decision(tenant, gateway):
    member = make_user()
    add_member(tenant, member)
    grant(tenant, member, "approver")
    assert gateway.resolve(human(member), tenant).permits("playbook.approve")
    set_status(tenant, member, "SUSPENDED")
    assert gateway.resolve(human(member), tenant) is None


def test_revoked_grant_stops_on_next_decision(tenant, owner, gateway):
    member = make_user()
    add_member(tenant, member)
    grant_id = grant(tenant, member, "approver")
    assert gateway.resolve(human(member), tenant).permits("playbook.approve")
    with scoped_tx(tenant) as conn:
        conn.execute(text("UPDATE member_grants SET revoked_at = now() WHERE id = :i"),
                     {"i": grant_id})
    assert not gateway.resolve(human(member), tenant).permits("playbook.approve")


def test_expired_grant_is_ignored(tenant, gateway):
    member = make_user()
    add_member(tenant, member)
    grant(tenant, member, "approver", expires_at=datetime.now(UTC) - timedelta(minutes=1))
    assert not gateway.resolve(human(member), tenant).permits("playbook.approve")


def test_grants_combine_within_a_tenant(tenant, gateway):
    member = make_user()
    add_member(tenant, member)
    grant(tenant, member, "approver")
    grant(tenant, member, "integration_admin")
    access = gateway.resolve(human(member), tenant)
    assert access.permits("playbook.approve")
    assert access.permits("source.credential.manage")


def test_grants_do_not_leak_across_tenants(tenant, gateway):
    member = make_user()
    other = make_tenant(make_user())
    add_member(tenant, member)
    add_member(other, member)
    grant(tenant, member, "approver")
    grant(other, member, "integration_admin")
    in_first = gateway.resolve(human(member), tenant)
    in_second = gateway.resolve(human(member), other)
    assert in_first.permits("playbook.approve")
    assert not in_first.permits("source.credential.manage")
    assert in_second.permits("source.credential.manage")
    assert not in_second.permits("playbook.approve")


def test_services_resolve_to_nothing_until_g5(tenant, gateway):
    worker = Principal(uuid4(), PrincipalKind.WORKER)
    assert gateway.resolve(worker, tenant) is None
