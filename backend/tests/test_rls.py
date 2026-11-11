"""Tenant isolation, owner invariant and permission resolution against real PostgreSQL.

These run as the runtime role, so RLS applies exactly as it does in production.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.authz.access import Decision, PostgresAccessGateway, Principal, PrincipalKind
from app.authz.catalogue import get_catalogue
from app.db import get_engine, global_tx, scoped_tx
from tests.domain_rows import DOMAIN_TABLES, seed_domain_rows

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


# Domain tables (task 3.7, spec 6.3)


@pytest.fixture
def two_seeded_tenants(owner: UUID, tenant: UUID) -> tuple[dict, UUID, dict]:
    """Tenant A and tenant B, each with one row in every domain table."""
    other_owner = make_user()
    other = make_tenant(other_owner)
    return seed_domain_rows(tenant, owner), other, seed_domain_rows(other, other_owner)


@pytest.mark.parametrize("table", DOMAIN_TABLES)
def test_tenant_a_reads_none_of_tenant_bs_domain_rows(tenant, two_seeded_tenants, table):
    mine, _, theirs = two_seeded_tenants
    with scoped_tx(tenant) as conn:
        found = conn.execute(
            text(f"SELECT count(*) FROM {table} WHERE id = :id"), {"id": theirs[table]}
        ).scalar()
        visible = conn.execute(text(f"SELECT id FROM {table}")).scalars().all()
    assert found == 0
    assert set(visible) <= {mine[table], mine["other_entity"]}


@pytest.mark.parametrize(
    ("table", "assignment"),
    [
        ("domain_runs", "finished_at = now()"),
        ("entity_resolution_decisions", "reason = 'x'"),
        ("domain_cases", "reason = 'x'"),
    ],
)
def test_tenant_a_updates_none_of_tenant_bs_domain_rows(
    tenant, two_seeded_tenants, table, assignment
):
    _, _, theirs = two_seeded_tenants
    with scoped_tx(tenant) as conn:
        result = conn.execute(
            text(f"UPDATE {table} SET {assignment} WHERE id = :id"), {"id": theirs[table]}
        )
    assert result.rowcount == 0


CROSS_TENANT_REFERENCES = {
    "link to their entity": (
        "INSERT INTO entity_source_links (id, tenant_id, entity_id, source_record_id, decision_id) "
        "VALUES (gen_random_uuid(), :tenant, :their_entity, :my_record, :my_decision)"
    ),
    "case on their run": (
        "INSERT INTO domain_cases (id, tenant_id, run_id, case_type, reconciliation_scope, "
        "severity, fingerprint, entity_ids, evidence, method, rule_version, version_refs) "
        "VALUES (gen_random_uuid(), :tenant, :their_run, 'TXN_MISSING', 'INTRA_ENTITY', "
        "'high', repeat('b', 64), '{}', '{}', 'm', '1', '{}')"
    ),
    "entity in their ontology": (
        "INSERT INTO domain_entities (id, tenant_id, ontology_id, class_iri, canonical_key, "
        "created_run_id) VALUES (gen_random_uuid(), :tenant, :their_ontology, 'finops:Order', "
        "'k', :my_run)"
    ),
    "observation of their record": (
        "INSERT INTO property_observations (id, tenant_id, entity_id, property_iri, "
        "source_record_id, source_id, raw_evidence_id, evidence_hash, field_path, mapping_id, "
        "mapping_version, raw_value, datatype, value_basis, temporal_context, transform, "
        "observed_at) VALUES (gen_random_uuid(), :tenant, :my_entity, 'finops:amount', "
        ":their_record, :my_source, :my_evidence, repeat('a', 64), '$.x', :my_mapping, 1, "
        "'1', 'decimal', 'gross', 't', '{}', now())"
    ),
    "relationship to their entity": (
        "INSERT INTO domain_relationship_instances (id, tenant_id, ontology_id, "
        "subject_entity_id, predicate_iri, object_entity_id, provenance, created_run_id) "
        "VALUES (gen_random_uuid(), :tenant, :my_ontology, :my_entity, 'finops:forOrder', "
        ":their_entity, '{}', :my_run)"
    ),
    "view on their entity": (
        "INSERT INTO resolved_property_views (id, tenant_id, run_id, entity_id, property_iri, "
        "status, mapping_versions, contributing_observation_ids) VALUES (gen_random_uuid(), "
        ":tenant, :my_run, :their_entity, 'finops:amount', 'UNRESOLVED', '{}', '{}')"
    ),
}


@pytest.mark.parametrize("statement", CROSS_TENANT_REFERENCES.values(),
                         ids=CROSS_TENANT_REFERENCES.keys())
def test_tenant_a_cannot_reference_tenant_bs_domain_rows(tenant, two_seeded_tenants, statement):
    mine, _, theirs = two_seeded_tenants
    params = {
        "tenant": tenant,
        "my_record": mine["record"],
        "my_decision": mine["entity_resolution_decisions"],
        "my_run": mine["domain_runs"],
        "my_entity": mine["domain_entities"],
        "my_ontology": mine["ontology"],
        "my_source": mine["source"],
        "my_evidence": mine["evidence"],
        "my_mapping": mine["mapping"],
        "their_entity": theirs["domain_entities"],
        "their_run": theirs["domain_runs"],
        "their_ontology": theirs["ontology"],
        "their_record": theirs["record"],
    }
    with pytest.raises(IntegrityError, match="foreign key"):
        with scoped_tx(tenant) as conn:
            conn.execute(text(statement), params)


@pytest.mark.parametrize("table", [
    "domain_entities", "entity_source_links", "property_observations",
    "resolved_property_views", "domain_relationship_instances",
])
@pytest.mark.parametrize("statement", ["UPDATE {t} SET tenant_id = tenant_id", "DELETE FROM {t}"])
def test_append_only_domain_tables_refuse_change(tenant, two_seeded_tenants, table, statement):
    with pytest.raises(DBAPIError, match="permission denied"):
        with scoped_tx(tenant) as conn:
            conn.execute(text(statement.format(t=table)))


@pytest.mark.parametrize("table", DOMAIN_TABLES)
def test_domain_rows_cannot_be_deleted(tenant, two_seeded_tenants, table):
    with pytest.raises(DBAPIError, match="permission denied"):
        with scoped_tx(tenant) as conn:
            conn.execute(text(f"DELETE FROM {table}"))


def test_domain_money_is_numeric_20_6():
    with global_tx() as conn:
        columns = conn.execute(
            text(
                """
                SELECT table_name, column_name, numeric_precision, numeric_scale
                FROM information_schema.columns
                WHERE table_schema = 'public' AND data_type = 'numeric'
                  AND table_name = ANY (:tables)
                """
            ),
            {"tables": list(DOMAIN_TABLES)},
        ).all()
    assert {(c.table_name, c.column_name) for c in columns} == {
        ("property_observations", "numeric_value"),
        ("resolved_property_views", "numeric_value"),
        ("domain_cases", "expected_amount"),
        ("domain_cases", "observed_amount"),
    }
    assert {(c.numeric_precision, c.numeric_scale) for c in columns} == {(20, 6)}


def test_case_money_keeps_six_places_and_needs_a_currency(tenant, two_seeded_tenants):
    mine, _, _ = two_seeded_tenants
    with scoped_tx(tenant) as conn:
        row = conn.execute(
            text("SELECT expected_amount, currency FROM domain_cases WHERE id = :id"),
            {"id": mine["domain_cases"]},
        ).one()
    assert (row.expected_amount, row.currency) == (Decimal("100.000001"), "USD")
    with pytest.raises(IntegrityError, match="check"):
        with scoped_tx(tenant) as conn:
            conn.execute(
                text(
                    "INSERT INTO domain_cases (id, tenant_id, run_id, case_type, "
                    "reconciliation_scope, severity, fingerprint, entity_ids, expected_amount, "
                    "evidence, method, rule_version, version_refs) VALUES (gen_random_uuid(), "
                    ":t, :run, 'AMOUNT_MISMATCH', 'INTRA_ENTITY', 'high', repeat('c', 64), "
                    "'{}', 10, '{}', 'm', '1', '{}')"
                ),
                {"t": tenant, "run": mine["domain_runs"]},
            )


@pytest.mark.parametrize("target", ["RESOLVED", "ACTION_PENDING", "CLOSED", "HUMAN_REVIEW"])
def test_database_refuses_a_case_transition_outside_9_3(tenant, two_seeded_tenants, target):
    mine, _, _ = two_seeded_tenants
    with pytest.raises(DBAPIError, match="not allowed"):
        with scoped_tx(tenant) as conn:
            conn.execute(
                text("UPDATE domain_cases SET status = :s WHERE id = :id"),
                {"s": target, "id": mine["domain_cases"]},
            )


def test_finished_run_is_immutable(tenant, two_seeded_tenants):
    mine, _, _ = two_seeded_tenants
    with scoped_tx(tenant) as conn:
        conn.execute(
            text("UPDATE domain_runs SET status = 'SUCCEEDED' WHERE id = :id"),
            {"id": mine["domain_runs"]},
        )
    with pytest.raises(DBAPIError, match="immutable"):
        with scoped_tx(tenant) as conn:
            conn.execute(
                text("UPDATE domain_runs SET status = 'FAILED' WHERE id = :id"),
                {"id": mine["domain_runs"]},
            )


def test_settled_identity_decision_cannot_be_rewritten(tenant, two_seeded_tenants):
    mine, _, _ = two_seeded_tenants
    with pytest.raises(DBAPIError, match="pending"):
        with scoped_tx(tenant) as conn:
            conn.execute(
                text("UPDATE entity_resolution_decisions SET status = 'REJECTED', "
                     "selected_entity_id = NULL, reviewer_id = :u, reviewed_at = now(), "
                     "reason = 'x' WHERE id = :id"),
                {"id": mine["entity_resolution_decisions"], "u": make_user()},
            )


def test_a_link_needs_a_recorded_decision(tenant, two_seeded_tenants):
    mine, _, _ = two_seeded_tenants
    with pytest.raises(IntegrityError, match="null value"):
        with scoped_tx(tenant) as conn:
            conn.execute(
                text("INSERT INTO entity_source_links (id, tenant_id, entity_id, "
                     "source_record_id) VALUES (gen_random_uuid(), :t, :e, :r)"),
                {"t": tenant, "e": mine["other_entity"], "r": mine["record"]},
            )


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


def test_a_worker_without_a_live_grant_resolves_to_nothing(tenant, gateway):
    """POL-09, AC-AUTHZ-06: the Worker's keys come only from an unconsumed, unexpired grant,
    which test_worker.py issues and consumes through the routes."""
    worker = Principal(uuid4(), PrincipalKind.WORKER)
    assert gateway.resolve(worker, tenant) is None
    assert gateway.resolve(Principal(worker.id, PrincipalKind.WORKER, grant_id=uuid4()),
                           tenant) is None
