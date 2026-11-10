"""Ownership transfer and audit reads through the real routes (task 1.4, spec 3.2)."""

import threading
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.authz.guard import TENANT_HEADER
from app.db import scoped_tx
from tests.support import Actor, add_member, app, clear_overrides, grant_set, owner_of

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
def _reset():
    yield
    clear_overrides()


@pytest.fixture
def owner() -> Actor:
    return Actor()


@pytest.fixture
def tenant(owner: Actor):
    return owner.create_tenant()


@pytest.fixture
def successor(tenant) -> Actor:
    actor = Actor()
    add_member(tenant, actor.id)
    return actor


def transfer(actor: Actor, tenant, target):
    return actor.request(
        "POST",
        f"/api/tenants/{tenant}/ownership-transfer",
        json={"new_owner_user_id": str(target)},
    )


def test_transfer_moves_the_pointer(owner, tenant, successor):
    assert transfer(owner, tenant, successor.id).status_code == 200
    assert owner_of(tenant) == successor.id


def test_transfer_writes_one_audit_event(owner, tenant, successor):
    transfer(owner, tenant, successor.id)
    with scoped_tx(tenant) as conn:
        row = conn.execute(
            text(
                "SELECT principal_id, detail FROM audit_logs "
                "WHERE action = 'tenant.ownership.transferred'"
            )
        ).one()
    assert row.principal_id == owner.id
    assert row.detail == {"previous_owner": str(owner.id), "new_owner": str(successor.id)}


def test_former_owner_loses_derived_permissions_immediately(owner, tenant, successor):
    assert "execution_mode.manage" in owner.held(tenant)
    transfer(owner, tenant, successor.id)
    assert owner.held(tenant) == set()
    assert {"execution_mode.manage", "tenant.ownership.transfer"} <= successor.held(tenant)


def test_former_owner_keeps_only_what_was_separately_granted(owner, tenant, successor):
    grant_set(tenant, owner.id, "business_admin")
    transfer(owner, tenant, successor.id)
    held = owner.held(tenant)
    assert "member.invite" in held
    assert "tenant.ownership.transfer" not in held
    assert "execution_mode.manage" not in held


def test_former_owner_cannot_transfer_again(owner, tenant, successor):
    transfer(owner, tenant, successor.id)
    response = transfer(owner, tenant, owner.id)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "MISSING_PERMISSION"


def test_business_admin_is_not_an_owner(owner, tenant, successor):
    grant_set(tenant, successor.id, "business_admin")
    response = transfer(successor, tenant, successor.id)
    assert response.status_code == 403
    assert response.json()["error"]["permission"] == "tenant.ownership.transfer"
    assert owner_of(tenant) == owner.id


def test_owner_cannot_transfer_to_themselves(owner, tenant):
    assert transfer(owner, tenant, owner.id).status_code == 422


def test_target_must_be_a_member(owner, tenant):
    outsider = Actor()
    assert transfer(owner, tenant, outsider.id).status_code == 404
    assert owner_of(tenant) == owner.id


@pytest.mark.parametrize("status", ["SUSPENDED", "REMOVED"])
def test_target_must_be_active(owner, tenant, status):
    member = Actor()
    add_member(tenant, member.id, status)
    assert transfer(owner, tenant, member.id).status_code == 404
    assert owner_of(tenant) == owner.id


def test_unknown_target_looks_like_any_other_non_member(owner, tenant):
    outsider = Actor()
    first = transfer(owner, tenant, outsider.id)
    second = transfer(owner, tenant, uuid4())
    assert first.status_code == second.status_code == 404
    assert first.content == second.content


def test_member_of_another_tenant_is_not_a_valid_target(owner, tenant):
    other = Actor()
    other.create_tenant()
    assert transfer(owner, tenant, other.id).status_code == 404


def test_body_cannot_carry_extra_fields(owner, tenant, successor):
    response = owner.request(
        "POST",
        f"/api/tenants/{tenant}/ownership-transfer",
        json={"new_owner_user_id": str(successor.id), "tenant_id": str(uuid4())},
    )
    assert response.status_code == 422


def test_concurrent_transfers_leave_exactly_one_owner(owner, tenant):
    first, second = Actor(), Actor()
    add_member(tenant, first.id)
    add_member(tenant, second.id)
    statuses = []
    owner.request("GET", "/api/me/permissions", params={"tenant_id": tenant})

    def attempt(target):
        client = TestClient(app, headers={TENANT_HEADER: str(tenant)})
        statuses.append(
            client.post(
                f"/api/tenants/{tenant}/ownership-transfer",
                json={"new_owner_user_id": str(target.id)},
            ).status_code
        )

    threads = [threading.Thread(target=attempt, args=(t,)) for t in (first, second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(statuses) == [200, 403]
    assert owner_of(tenant) in {first.id, second.id}
    with scoped_tx(tenant) as conn:
        events = conn.execute(
            text("SELECT count(*) FROM audit_logs WHERE action = 'tenant.ownership.transferred'")
        ).scalar()
    assert events == 1


def audit_id(tenant):
    with scoped_tx(tenant) as conn:
        return conn.execute(
            text("SELECT id FROM audit_logs WHERE action = 'tenant.created'")
        ).scalar_one()


def test_audit_record_can_be_read(owner, tenant):
    row_id = audit_id(tenant)
    response = owner.request("GET", f"/api/audit/{row_id}", tenant=tenant)
    assert response.status_code == 200
    assert response.json()["action"] == "tenant.created"
    assert response.json()["principal_id"] == str(owner.id)


def test_audit_read_needs_the_permission(owner, tenant, successor):
    response = successor.request("GET", f"/api/audit/{audit_id(tenant)}", tenant=tenant)
    assert response.status_code == 403


def test_another_tenants_audit_record_is_a_404(owner, tenant):
    other = Actor()
    other_tenant = other.create_tenant()
    response = other.request("GET", f"/api/audit/{audit_id(tenant)}", tenant=other_tenant)
    assert response.status_code == 404
