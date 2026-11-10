"""AC-IAM-01..11 and AC-TEN-01..03 through the routes (task 1.13, spec 21).

TRACE maps each criterion to the tests that hold its evidence, and
`test_every_traced_test_exists` keeps the map honest when tests are renamed. The tests below
fill what the module-level suites leave out: the same person under different grants in two
tenants, and the full cross-tenant sweep over every way a browser can name a tenant.

Parts of AC-TEN-01 and AC-TEN-02 name things not built in this slice: ontology versions,
source configurations, records, playbooks, filters and agent tool arguments. Their tasks
(2.7, 3.7, 4.5, 5.1, 5.7) extend the isolation evidence; the RLS suite in test_rls.py already
covers every tenant-owned table.
"""

import importlib
import re
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from app.db import scoped_tx
from app.mail import get_mail_sender
from tests.support import Actor, add_member, app, clear_overrides, grant_set

TRACE = {
    "AC-IAM-01": [
        "test_identity::test_creator_becomes_the_sole_active_owner",
        "test_identity::test_owner_permissions_derive_from_the_pointer_and_are_not_a_grant",
        "test_identity::test_failed_creation_leaves_nothing_behind",
    ],
    "AC-IAM-02": [
        "test_invitations::test_invitation_is_pending_and_emailed",
        "test_invitations::test_matching_verified_email_joins_with_the_invited_grants",
        "test_invitations::test_grantor_must_hold_the_grant_authority",
    ],
    "AC-IAM-03": [
        "test_invitations::test_matching_verified_email_joins_with_the_invited_grants",
        "test_invitations::test_different_email_cannot_accept_and_the_invitation_survives",
        "test_invitations::test_unverified_account_cannot_accept",
        "test_invitations::test_expired_token_fails_and_is_recorded",
    ],
    "AC-IAM-04": [
        "test_invitations::test_existing_user_joins_a_second_tenant_with_independent_grants",
    ],
    "AC-IAM-05": [
        "test_invitations::test_token_cannot_be_replayed",
        "test_invitations::test_revoked_token_fails",
        "test_invitations::test_expired_token_fails_and_is_recorded",
        "test_invitations::test_resend_rotates_the_token_and_invalidates_the_old_link",
    ],
    "AC-IAM-06": [
        "test_invitations::test_members_without_the_key_cannot_manage_members",
        "test_invitations::test_non_member_sees_only_404",
        "test_invitations::test_another_tenants_invitation_cannot_be_managed",
        "test_invitations::test_member_cannot_elevate_themselves",
        "test_invitations::test_suspension_needs_the_removal_key",
    ],
    "AC-IAM-07": [
        "test_invitations::test_added_grant_applies_on_the_next_request",
        "test_invitations::test_revoked_grant_stops_on_the_next_request",
        "test_invitations::test_removed_member_loses_access_on_the_next_request",
        "test_invitations::test_suspension_applies_on_the_next_request_and_reinstating_restores",
        "test_identity::test_disabled_account_loses_its_session_on_the_next_request",
    ],
    "AC-IAM-08": [
        "test_iam_acceptance::test_same_person_holds_different_sets_in_each_tenant",
        "test_rls::test_grants_do_not_leak_across_tenants",
    ],
    "AC-IAM-09": [
        "test_invitations::test_delivery_failure_is_visible_and_activates_nobody",
        "test_invitations::test_failed_delivery_can_be_retried",
    ],
    "AC-IAM-10": [
        "test_invitations::test_every_lifecycle_event_is_audited",
        "test_invitations::test_suspend_and_reinstate_are_audited",
    ],
    "AC-IAM-11": [
        "test_rls::test_catalogue_is_seeded_without_identities",
        "test_iam_acceptance::test_seed_data_names_no_people",
    ],
    "AC-TEN-01": [
        "test_iam_acceptance::test_two_businesses_share_nothing",
    ],
    "AC-TEN-02": [
        "test_iam_acceptance::test_another_tenant_is_404_however_it_is_named",
        "test_iam_acceptance::test_another_tenants_ids_are_404_under_your_own_tenant",
        "test_rls::test_tenant_a_cannot_read_tenant_b",
        "test_rls::test_tenant_a_cannot_update_tenant_b",
    ],
    "AC-TEN-03": [
        "test_iam_acceptance::test_path_tenant_wins_over_the_header",
        "test_invitations::test_body_cannot_carry_a_tenant",
        "test_identity::test_client_cannot_choose_tenant_or_owner",
        "test_rls::test_tenant_context_does_not_outlive_the_transaction",
    ],
}

REPO = Path(__file__).resolve().parents[2]
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


@pytest.mark.parametrize("criterion", sorted(TRACE))
def test_every_traced_test_exists(criterion):
    for entry in TRACE[criterion]:
        module, name = entry.split("::")
        assert callable(getattr(importlib.import_module(f"tests.{module}"), name, None)), entry


def test_every_criterion_is_traced():
    expected = {f"AC-IAM-{n:02}" for n in range(1, 12)} | {f"AC-TEN-{n:02}" for n in (1, 2, 3)}
    assert set(TRACE) == expected


def test_seed_data_names_no_people():
    """AC-IAM-11: no pre-seeded accounts, hard-coded emails or email-to-set mappings."""
    seeded = [
        REPO / "backend/app/authz/permission_sets.yaml",
        REPO / "backend/app/authz/permissions.yaml",
        REPO / "backend/app/authz/seed.py",
    ]
    for path in seeded:
        assert not EMAIL.findall(path.read_text(encoding="utf-8")), path


# Through the routes


class DiscardMail:
    def send(self, to: str, subject: str, body: str) -> None:
        pass


@pytest.fixture(autouse=True)
def _reset():
    # Never reach a configured SMTP relay from tests.
    app.dependency_overrides[get_mail_sender] = DiscardMail
    yield
    clear_overrides()


def new_tenant(actor: Actor, name: str) -> UUID:
    response = actor.request("POST", "/api/tenants", json={"name": name})
    assert response.status_code == 201
    return UUID(response.json()["id"])


def invite(actor: Actor, tenant: UUID) -> str:
    grants = [{"permission_set_key": "approver"}]
    response = actor.request(
        "POST",
        f"/api/tenants/{tenant}/invitations",
        json={"email": f"{uuid4().hex}@example.com", "grants": grants},
    )
    assert response.status_code == 201
    return response.json()["id"]


@pytest.mark.db
def test_same_person_holds_different_sets_in_each_tenant():
    person = Actor()
    first, second = Actor().create_tenant(), Actor().create_tenant()
    for tenant, set_key in ((first, "approver"), (second, "integration_admin")):
        add_member(tenant, person.id)
        grant_set(tenant, person.id, set_key)
    in_first, in_second = person.held(first), person.held(second)
    assert "playbook.approve" in in_first and "playbook.approve" not in in_second
    assert "source.credential.manage" in in_second
    assert "source.credential.manage" not in in_first


@pytest.mark.db
def test_two_businesses_share_nothing():
    a, b = Actor(), Actor()
    tenant_a, tenant_b = new_tenant(a, "A"), new_tenant(b, "B")
    invite(a, tenant_a)
    invite(b, tenant_b)

    def members(actor, tenant):
        return {m["user_id"] for m in actor.request("GET", f"/api/tenants/{tenant}/members").json()}

    def invited(actor, tenant):
        return {i["id"] for i in actor.request("GET", f"/api/tenants/{tenant}/invitations").json()}

    assert members(a, tenant_a) == {str(a.id)} and members(b, tenant_b) == {str(b.id)}
    assert len(invited(a, tenant_a)) == len(invited(b, tenant_b)) == 1
    assert not invited(a, tenant_a) & invited(b, tenant_b)
    for tenant in (tenant_a, tenant_b):
        with scoped_tx(tenant) as conn:
            foreign = conn.execute(
                text("SELECT count(*) FROM audit_logs WHERE tenant_id <> :t"), {"t": tenant}
            ).scalar_one()
            own = conn.execute(text("SELECT count(*) FROM audit_logs")).scalar_one()
        assert foreign == 0 and own > 0
    assert {t["id"] for t in a.request("GET", "/api/me/tenants").json()["tenants"]} == {
        str(tenant_a)
    }


@pytest.mark.db
def test_another_tenant_is_404_however_it_is_named():
    a, b = Actor(), Actor()
    a.create_tenant()
    tenant_b = b.create_tenant()
    attempts = [
        a.request("GET", f"/api/tenants/{tenant_b}/members"),
        a.request("POST", f"/api/tenants/{tenant_b}/members/{b.id}/suspend"),
        a.request("GET", "/api/permission-sets", tenant=tenant_b),
        a.request("GET", "/api/me/permissions", params={"tenant_id": tenant_b}),
    ]
    for response in attempts:
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.db
def test_another_tenants_ids_are_404_under_your_own_tenant():
    a, b = Actor(), Actor()
    tenant_a, tenant_b = a.create_tenant(), b.create_tenant()
    invitation_b = invite(b, tenant_b)
    assert a.request(
        "POST", f"/api/tenants/{tenant_a}/invitations/{invitation_b}/revoke"
    ).status_code == 404
    assert a.request("DELETE", f"/api/tenants/{tenant_a}/members/{b.id}").status_code == 404
    assert a.request("POST", f"/api/tenants/{tenant_a}/members/{b.id}/suspend").status_code == 404
    still_pending = b.request("GET", f"/api/tenants/{tenant_b}/invitations").json()
    assert [i["id"] for i in still_pending] == [invitation_b]


@pytest.mark.db
def test_path_tenant_wins_over_the_header():
    """The header only selects where the path has no tenant; it never redirects one."""
    a, b = Actor(), Actor()
    tenant_a, tenant_b = a.create_tenant(), b.create_tenant()
    response = a.request("GET", f"/api/tenants/{tenant_a}/members", tenant=tenant_b)
    assert response.status_code == 200
    assert [m["user_id"] for m in response.json()] == [str(a.id)]
