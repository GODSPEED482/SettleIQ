"""Invitations and member management through the real routes (task 1.5, spec 3.1)."""

import re
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.auth.tokens import hash_token
from app.authz.access import EffectiveAccess, GrantRow
from app.authz.catalogue import get_catalogue
from app.db import global_tx, scoped_tx
from app.errors import ApiError
from app.mail import MailDeliveryError, get_mail_sender
from app.tenancy import grants as grant_policy
from app.tenancy.grants import GrantInput
from tests.support import (
    Actor,
    add_member,
    app,
    clear_overrides,
    grant_set,
    member_status,
    owner_of,
)

pytestmark = pytest.mark.db


class FakeMail:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.fail = False

    def send(self, to: str, subject: str, body: str) -> None:
        if self.fail:
            raise MailDeliveryError("TimeoutError")
        self.sent.append((to, body))

    def last_token(self) -> str:
        return re.search(r"#token=(\S+)", self.sent[-1][1]).group(1)


@pytest.fixture
def mail():
    fake = FakeMail()
    app.dependency_overrides[get_mail_sender] = lambda: fake
    return fake


@pytest.fixture(autouse=True)
def _reset():
    yield
    clear_overrides()


@pytest.fixture
def owner() -> Actor:
    return Actor()


@pytest.fixture
def tenant(owner):
    return owner.create_tenant()


def email() -> str:
    return f"{uuid4().hex}@example.com"


def invite(actor, tenant, address, set_key="approver", **grant_fields):
    return actor.request(
        "POST",
        f"/api/tenants/{tenant}/invitations",
        json={"email": address, "grants": [{"permission_set_key": set_key, **grant_fields}]},
    )


def accept(actor, token):
    return actor.request("POST", f"/api/invitations/{token}/accept")


def invitations(actor, tenant) -> list[dict]:
    return actor.request("GET", f"/api/tenants/{tenant}/invitations").json()


def invited_person(owner, tenant, mail, set_key="approver") -> Actor:
    """An existing account that has joined through a real invitation."""
    address = email()
    person = Actor(address)
    assert invite(owner, tenant, address, set_key).status_code == 201
    assert accept(person, mail.last_token()).status_code == 200
    return person


def audit_actions(tenant) -> set[str]:
    with scoped_tx(tenant) as conn:
        return set(conn.execute(text("SELECT action FROM audit_logs")).scalars())


def change(actor, tenant, user_id, **body):
    return actor.request(
        "PATCH", f"/api/tenants/{tenant}/members/{user_id}/grants", json=body
    )


def remove(actor, tenant, user_id):
    return actor.request("DELETE", f"/api/tenants/{tenant}/members/{user_id}")


def policy(response) -> str:
    return response.json()["error"]["policy"]


# Creating and accepting


def test_invitation_is_pending_and_emailed(owner, tenant, mail):
    address = email()
    body = invite(owner, tenant, address).json()
    assert body["status"] == "PENDING"
    assert body["delivery_status"] == "SENT"
    assert mail.sent[0][0] == address


def test_token_is_stored_only_as_a_hash(owner, tenant, mail):
    invite(owner, tenant, email())
    raw = mail.last_token()
    with scoped_tx(tenant) as conn:
        stored = conn.execute(text("SELECT token_hash FROM tenant_invitations")).scalar_one()
    assert stored == hash_token(raw) != raw


def test_list_never_exposes_the_token(owner, tenant, mail):
    invite(owner, tenant, email())
    response = owner.request("GET", f"/api/tenants/{tenant}/invitations")
    assert "token" not in response.text
    assert mail.last_token() not in response.text


def test_matching_verified_email_joins_with_the_invited_grants(owner, tenant, mail):
    address = email()
    person = Actor(address)
    invite(owner, tenant, address)
    response = accept(person, mail.last_token())
    assert response.json() == {"tenant_id": str(tenant), "status": "ACCEPTED"}
    assert member_status(tenant, person.id) == "ACTIVE"
    assert "playbook.approve" in person.held(tenant)


def test_invited_email_is_normalized(owner, tenant, mail):
    address = email()
    person = Actor(address)
    invite(owner, tenant, address.replace("@example.com", "@EXAMPLE.com").upper()
           .replace("EXAMPLE.COM", "example.com"))
    assert accept(person, mail.last_token()).status_code == 200


def test_different_email_cannot_accept_and_the_invitation_survives(owner, tenant, mail):
    address = email()
    invite(owner, tenant, address)
    token = mail.last_token()
    assert accept(Actor(), token).status_code == 404
    assert accept(Actor(address), token).status_code == 200


def test_unverified_account_cannot_accept(owner, tenant, mail):
    address = email()
    person = Actor(address)
    with global_tx() as conn:
        conn.execute(
            text("UPDATE users SET email_verified_at = NULL WHERE id = :i"), {"i": person.id}
        )
    invite(owner, tenant, address)
    assert accept(person, mail.last_token()).status_code == 404
    assert member_status(tenant, person.id) is None


def test_every_failed_acceptance_looks_the_same(owner, tenant, mail):
    invite(owner, tenant, email())
    wrong_person = accept(Actor(), mail.last_token())
    unknown = accept(Actor(), "not-a-real-token")
    assert wrong_person.status_code == unknown.status_code == 404
    assert wrong_person.content == unknown.content


def test_existing_user_joins_a_second_tenant_with_independent_grants(owner, tenant, mail):
    person = invited_person(owner, tenant, mail, "approver")
    other_owner = Actor()
    other = other_owner.create_tenant()
    with global_tx() as conn:
        address = conn.execute(
            text("SELECT email FROM users WHERE id = :i"), {"i": person.id}
        ).scalar_one()
        users = conn.execute(
            text("SELECT count(*) FROM users WHERE email = :e"), {"e": address}
        ).scalar()
    invite(other_owner, other, address, "integration_admin")
    assert accept(person, mail.last_token()).status_code == 200
    assert users == 1
    assert "playbook.approve" in person.held(tenant)
    assert "playbook.approve" not in person.held(other)
    assert "source.credential.manage" in person.held(other)
    assert "source.credential.manage" not in person.held(tenant)


# Invalid tokens


def test_token_cannot_be_replayed(owner, tenant, mail):
    address = email()
    person = Actor(address)
    invite(owner, tenant, address)
    token = mail.last_token()
    assert accept(person, token).status_code == 200
    assert accept(person, token).status_code == 404


def test_revoked_token_fails(owner, tenant, mail):
    address = email()
    invite_id = invite(owner, tenant, address).json()["id"]
    token = mail.last_token()
    path = f"/api/tenants/{tenant}/invitations/{invite_id}/revoke"
    assert owner.request("POST", path).status_code == 200
    assert accept(Actor(address), token).status_code == 404
    assert invitations(owner, tenant)[0]["status"] == "REVOKED"


def test_expired_token_fails_and_is_recorded(owner, tenant, mail):
    address = email()
    invite(owner, tenant, address)
    with scoped_tx(tenant) as conn:
        conn.execute(text("UPDATE tenant_invitations SET expires_at = now() - interval '1 minute'"))
    assert accept(Actor(address), mail.last_token()).status_code == 404
    with scoped_tx(tenant) as conn:
        assert conn.execute(text("SELECT status FROM tenant_invitations")).scalar_one() == "EXPIRED"


def test_resend_rotates_the_token_and_invalidates_the_old_link(owner, tenant, mail):
    address = email()
    invite_id = invite(owner, tenant, address).json()["id"]
    old = mail.last_token()
    path = f"/api/tenants/{tenant}/invitations/{invite_id}/resend"
    assert owner.request("POST", path).status_code == 200
    new = mail.last_token()
    assert new != old
    person = Actor(address)
    assert accept(person, old).status_code == 404
    assert accept(person, new).status_code == 200


def test_used_invitation_cannot_be_resent_or_revoked(owner, tenant, mail):
    address = email()
    invite_id = invite(owner, tenant, address).json()["id"]
    accept(Actor(address), mail.last_token())
    for action in ("resend", "revoke"):
        path = f"/api/tenants/{tenant}/invitations/{invite_id}/{action}"
        assert owner.request("POST", path).status_code == 409


# Delivery


def test_delivery_failure_is_visible_and_activates_nobody(owner, tenant, mail):
    mail.fail = True
    address = email()
    person = Actor(address)
    assert invite(owner, tenant, address).json()["delivery_status"] == "FAILED"
    listed = invitations(owner, tenant)[0]
    assert listed["delivery_status"] == "FAILED"
    assert listed["last_delivery_error"] == "TimeoutError"
    assert member_status(tenant, person.id) is None


def test_failed_delivery_can_be_retried(owner, tenant, mail):
    mail.fail = True
    address = email()
    invite_id = invite(owner, tenant, address).json()["id"]
    mail.fail = False
    response = owner.request("POST", f"/api/tenants/{tenant}/invitations/{invite_id}/resend")
    assert response.json()["delivery_status"] == "SENT"
    listed = invitations(owner, tenant)[0]
    assert listed["delivery_attempts"] == 2
    assert listed["last_delivery_error"] is None
    assert accept(Actor(address), mail.last_token()).status_code == 200


# Duplicates


def test_existing_member_cannot_be_invited(owner, tenant, mail):
    address = email()
    person = Actor(address)
    invite(owner, tenant, address)
    accept(person, mail.last_token())
    response = invite(owner, tenant, address)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ALREADY_MEMBER"


def test_second_pending_invitation_is_refused(owner, tenant, mail):
    address = email()
    invite(owner, tenant, address)
    response = invite(owner, tenant, address)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INVITATION_EXISTS"


def test_expired_invitation_does_not_block_a_new_one(owner, tenant, mail):
    address = email()
    invite(owner, tenant, address)
    with scoped_tx(tenant) as conn:
        conn.execute(text("UPDATE tenant_invitations SET expires_at = now() - interval '1 minute'"))
    assert invite(owner, tenant, address).status_code == 201


# Grant policy


def test_owner_set_cannot_be_invited(owner, tenant, mail):
    response = invite(owner, tenant, email(), "tenant_owner")
    assert response.status_code == 403
    assert policy(response) == "POL-13"
    assert mail.sent == []


@pytest.mark.parametrize(
    "set_key", ["investigation_agent", "playbook_worker", "platform_operator",
                "platform_security_approver"]
)
def test_service_and_platform_sets_cannot_be_invited(owner, tenant, mail, set_key):
    response = invite(owner, tenant, email(), set_key)
    assert response.status_code == 403
    assert policy(response) == "POL-03"


def test_business_admin_cannot_assign_the_owner_set(owner, tenant, mail):
    admin = invited_person(owner, tenant, mail, "business_admin")
    assert policy(invite(admin, tenant, email(), "tenant_owner")) == "POL-13"
    target = invited_person(owner, tenant, mail)
    response = change(admin, tenant, target.id, add=[{"permission_set_key": "tenant_owner"}])
    assert policy(response) == "POL-13"


def test_unknown_set_is_rejected(owner, tenant, mail):
    assert invite(owner, tenant, email(), "made_up").status_code == 422


@pytest.mark.parametrize("extra", ["tenant_id", "conditions", "owner", "expires_at"])
def test_grant_input_refuses_unknown_fields(owner, tenant, mail, extra):
    assert invite(owner, tenant, email(), **{extra: "x"}).status_code == 422


def test_body_cannot_carry_a_tenant(owner, tenant, mail):
    response = owner.request(
        "POST",
        f"/api/tenants/{tenant}/invitations",
        json={"email": email(), "tenant_id": str(uuid4()),
              "grants": [{"permission_set_key": "approver"}]},
    )
    assert response.status_code == 422


def test_invitation_needs_at_least_one_grant(owner, tenant, mail):
    response = owner.request(
        "POST", f"/api/tenants/{tenant}/invitations", json={"email": email(), "grants": []}
    )
    assert response.status_code == 422


def test_undeclared_scope_is_rejected_not_ignored(owner, tenant, mail):
    response = invite(owner, tenant, email(), "approver", scope_type="source", scope_id="s")
    assert response.status_code == 422


def test_scope_needs_both_parts(owner, tenant, mail):
    assert invite(owner, tenant, email(), "approver", scope_type="source").status_code == 422


def test_grantor_must_hold_the_grant_authority(owner, tenant):
    access = EffectiveAccess([GrantRow("member.invite", "ALLOW")], get_catalogue())
    with scoped_tx(tenant) as conn, pytest.raises(ApiError) as caught:
        grant_policy.check_grants(conn, access, [GrantInput(permission_set_key="approver")])
    assert caught.value.details == {"policy": "POL-02"}


def test_inviter_who_lost_authority_cannot_complete_a_later_acceptance(owner, tenant, mail):
    admin = invited_person(owner, tenant, mail, "business_admin")
    address = email()
    invite(admin, tenant, address)
    token = mail.last_token()
    with scoped_tx(tenant) as conn:
        conn.execute(
            text("UPDATE member_grants SET revoked_at = now() WHERE user_id = :u"),
            {"u": admin.id},
        )
    person = Actor(address)
    assert accept(person, token).status_code == 404
    assert member_status(tenant, person.id) is None


def test_removed_inviter_invalidates_the_invitation(owner, tenant, mail):
    admin = invited_person(owner, tenant, mail, "business_admin")
    address = email()
    invite(admin, tenant, address)
    token = mail.last_token()
    remove(owner, tenant, admin.id)
    assert accept(Actor(address), token).status_code == 404


# Unauthorized callers


@pytest.mark.parametrize("set_key", ["domain_operator", "approver", "integration_admin"])
def test_members_without_the_key_cannot_manage_members(owner, tenant, mail, set_key):
    member = invited_person(owner, tenant, mail, set_key)
    other = invited_person(owner, tenant, mail)
    assert invite(member, tenant, email()).status_code == 403
    assert member.request("GET", f"/api/tenants/{tenant}/members").status_code == 403
    assert change(member, tenant, other.id, revoke=[str(uuid4())]).status_code == 403
    assert remove(member, tenant, other.id).status_code == 403


def test_non_member_sees_only_404(owner, tenant, mail):
    outsider = Actor()
    assert invite(outsider, tenant, email()).status_code == 404
    assert outsider.request("GET", f"/api/tenants/{tenant}/members").status_code == 404


def test_another_tenants_invitation_cannot_be_managed(owner, tenant, mail):
    invite_id = invite(owner, tenant, email()).json()["id"]
    other = Actor()
    other_tenant = other.create_tenant()
    for action in ("resend", "revoke"):
        path = f"/api/tenants/{other_tenant}/invitations/{invite_id}/{action}"
        assert other.request("POST", path).status_code == 404
    assert invitations(other, other_tenant) == []


# Changing grants


def test_listing_shows_members_and_grants(owner, tenant, mail):
    person = invited_person(owner, tenant, mail, "approver")
    rows = {r["user_id"]: r for r in
            owner.request("GET", f"/api/tenants/{tenant}/members").json()}
    assert rows[str(owner.id)]["owns_tenant"] is True
    assert rows[str(owner.id)]["grants"] == []
    assert rows[str(person.id)]["owns_tenant"] is False
    assert rows[str(person.id)]["grants"][0]["permission_set_key"] == "approver"


def first_grant_id(owner, tenant, user_id) -> str:
    rows = {r["user_id"]: r for r in
            owner.request("GET", f"/api/tenants/{tenant}/members").json()}
    return rows[str(user_id)]["grants"][0]["id"]


def test_added_grant_applies_on_the_next_request(owner, tenant, mail):
    person = invited_person(owner, tenant, mail, "approver")
    assert "source.credential.manage" not in person.held(tenant)
    response = change(owner, tenant, person.id, add=[{"permission_set_key": "integration_admin"}])
    assert response.status_code == 200
    assert "source.credential.manage" in person.held(tenant)


def test_revoked_grant_stops_on_the_next_request(owner, tenant, mail):
    person = invited_person(owner, tenant, mail, "approver")
    grant_id = first_grant_id(owner, tenant, person.id)
    assert change(owner, tenant, person.id, revoke=[grant_id]).status_code == 200
    assert "playbook.approve" not in person.held(tenant)


def test_adding_the_same_grant_twice_creates_one(owner, tenant, mail):
    person = invited_person(owner, tenant, mail, "approver")
    change(owner, tenant, person.id, add=[{"permission_set_key": "approver"}])
    with scoped_tx(tenant) as conn:
        assert conn.execute(
            text("SELECT count(*) FROM member_grants WHERE user_id = :u"), {"u": person.id}
        ).scalar() == 1


def test_member_cannot_elevate_themselves(owner, tenant, mail):
    admin = invited_person(owner, tenant, mail, "business_admin")
    response = change(admin, tenant, admin.id, add=[{"permission_set_key": "approver"}])
    assert response.status_code == 403
    assert policy(response) == "POL-05"
    assert "playbook.approve" not in admin.held(tenant)


def test_owner_cannot_elevate_themselves_either(owner, tenant, mail):
    response = change(owner, tenant, owner.id, add=[{"permission_set_key": "approver"}])
    assert policy(response) == "POL-05"


def test_member_can_give_up_their_own_grant(owner, tenant, mail):
    admin = invited_person(owner, tenant, mail, "business_admin")
    grant_id = first_grant_id(owner, tenant, admin.id)
    assert change(admin, tenant, admin.id, revoke=[grant_id]).status_code == 200


def test_cannot_revoke_a_grant_belonging_to_someone_else(owner, tenant, mail):
    first = invited_person(owner, tenant, mail)
    second = invited_person(owner, tenant, mail)
    grant_id = first_grant_id(owner, tenant, first.id)
    assert change(owner, tenant, second.id, revoke=[grant_id]).status_code == 404
    assert "playbook.approve" in first.held(tenant)


def test_grant_change_needs_a_change(owner, tenant, mail):
    person = invited_person(owner, tenant, mail)
    assert change(owner, tenant, person.id).status_code == 422


def test_cannot_change_grants_of_a_non_member(owner, tenant, mail):
    response = change(owner, tenant, Actor().id, add=[{"permission_set_key": "approver"}])
    assert response.status_code == 404


# Removing members


def test_owner_cannot_be_removed(owner, tenant, mail):
    admin = invited_person(owner, tenant, mail, "business_admin")
    response = remove(admin, tenant, owner.id)
    assert response.status_code == 403
    assert policy(response) == "POL-04"
    assert owner_of(tenant) == owner.id
    assert member_status(tenant, owner.id) == "ACTIVE"


def test_owner_cannot_remove_themselves(owner, tenant, mail):
    assert policy(remove(owner, tenant, owner.id)) == "POL-04"


def test_last_holder_of_grant_authority_is_protected(owner, tenant, mail):
    held = owner.held(tenant)
    assert "member.role.assign" in held
    assert remove(owner, tenant, owner.id).status_code == 403
    assert "member.role.assign" in owner.held(tenant)


def test_removed_member_loses_access_on_the_next_request(owner, tenant, mail):
    person = invited_person(owner, tenant, mail, "approver")
    assert remove(owner, tenant, person.id).json()["status"] == "REMOVED"
    assert member_status(tenant, person.id) == "REMOVED"
    response = person.request("GET", "/api/me/permissions", params={"tenant_id": tenant})
    assert response.status_code == 404


def test_removal_revokes_grants_so_rejoining_starts_clean(owner, tenant, mail):
    address = email()
    person = Actor(address)
    invite(owner, tenant, address, "approver")
    accept(person, mail.last_token())
    remove(owner, tenant, person.id)
    invite(owner, tenant, address, "integration_admin")
    assert accept(person, mail.last_token()).status_code == 200
    held = person.held(tenant)
    assert "source.credential.manage" in held
    assert "playbook.approve" not in held


def test_member_without_the_key_cannot_leave_through_this_route(owner, tenant, mail):
    person = invited_person(owner, tenant, mail)
    assert remove(person, tenant, person.id).status_code == 403


def test_business_admin_can_leave(owner, tenant, mail):
    admin = invited_person(owner, tenant, mail, "business_admin")
    assert remove(admin, tenant, admin.id).status_code == 200


def test_removing_twice_is_a_404(owner, tenant, mail):
    person = invited_person(owner, tenant, mail)
    remove(owner, tenant, person.id)
    assert remove(owner, tenant, person.id).status_code == 404


def test_suspended_member_can_be_removed(owner, tenant):
    member = Actor()
    add_member(tenant, member.id, "SUSPENDED")
    grant_set(tenant, member.id, "approver")
    assert remove(owner, tenant, member.id).status_code == 200


# Audit and isolation


def test_every_lifecycle_event_is_audited(owner, tenant, mail):
    address = email()
    invite_id = invite(owner, tenant, address).json()["id"]
    owner.request("POST", f"/api/tenants/{tenant}/invitations/{invite_id}/resend")
    person = Actor(address)
    accept(person, mail.last_token())
    change(owner, tenant, person.id, add=[{"permission_set_key": "integration_admin"}])
    second = invite(owner, tenant, email()).json()["id"]
    owner.request("POST", f"/api/tenants/{tenant}/invitations/{second}/revoke")
    remove(owner, tenant, person.id)
    assert {
        "invitation.created", "invitation.resent", "invitation.accepted", "invitation.revoked",
        "member.grants.changed", "member.removed",
    } <= audit_actions(tenant)


def test_audit_records_never_contain_the_token(owner, tenant, mail):
    invite(owner, tenant, email())
    token = mail.last_token()
    with scoped_tx(tenant) as conn:
        details = conn.execute(text("SELECT detail::text FROM audit_logs")).scalars().all()
    assert not [detail for detail in details if token in detail or hash_token(token) in detail]


def test_lookup_returns_only_a_tenant_id(owner, tenant, mail):
    invite(owner, tenant, email())
    with global_tx() as conn:
        found = conn.execute(
            text("SELECT invitation_tenant_by_token_hash(:h)"),
            {"h": hash_token(mail.last_token())},
        ).scalar_one()
        missing = conn.execute(
            text("SELECT invitation_tenant_by_token_hash('nope')")
        ).scalar_one()
        visible = conn.execute(text("SELECT count(*) FROM tenant_invitations")).scalar()
    assert (found, missing, visible) == (tenant, None, 0)


def test_invitations_cannot_be_deleted_by_the_application(owner, tenant, mail):
    invite(owner, tenant, email())
    with pytest.raises(DBAPIError, match="permission denied"):
        with scoped_tx(tenant) as conn:
            conn.execute(text("DELETE FROM tenant_invitations"))
