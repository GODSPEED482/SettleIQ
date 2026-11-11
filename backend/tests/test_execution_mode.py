"""Exact-scope execution mode (task 5.3; spec 9.5, AC-EXEC-MODE-01, 02, AC-AUTHZ-16).

An absent scope is SHADOW, with no inheritance. The API accepts only an exact, declared scope.
LIVE needs a POL-14 verdict for that exact scope; SHADOW never does. Every switch is audited.
"""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.db import scoped_tx
from app.domain_packs.playbooks import get_playbooks
from app.execution.mode import resolve_mode
from app.execution.scope import ExactScope, playbook_scope
from app.release import verifier
from app.release.verifier import Verdict
from tests.support import add_member, grant_set, make_user
from tests.test_semantics_store import Member

pytestmark = pytest.mark.db

MERCHANT = {"domain_pack_id": "finops", "connector_id": "internal-merchant",
            "action_capability": "merchant.payment_status.correct"}
REFUND = {"domain_pack_id": "finops", "connector_id": "paypal",
          "action_capability": "refunds.create"}


def scope_of(tenant: UUID, body: dict) -> ExactScope:
    return ExactScope(tenant, body["domain_pack_id"], "0.2.0", body["connector_id"],
                      body["action_capability"])


def mode_of(tenant: UUID, body: dict) -> str:
    with scoped_tx(tenant) as conn:
        return resolve_mode(conn, scope_of(tenant, body)).mode


@pytest.fixture
def owner() -> Member:
    member = Member()
    assert member.activate("finops").status_code == 200
    return member


def put(member: Member, body: dict, mode: str):
    path = f"/api/tenants/{member.tenant}/execution-mode"
    return member.request("PUT", path, json={**body, "mode": mode})


@pytest.fixture
def attested(monkeypatch):
    """A verifier that accepts exactly one scope, as a real attestation would."""
    attestation = uuid4()

    def verify(conn, scope):
        if (scope.connector_id, scope.action_capability) == (MERCHANT["connector_id"],
                                                             MERCHANT["action_capability"]):
            return Verdict(True, None, attestation)
        return Verdict(False, "CAPABILITY_MEMBERSHIP")

    monkeypatch.setattr(verifier, "verify", verify)
    return attestation


def audits(tenant: UUID, action: str) -> list[dict]:
    with scoped_tx(tenant) as conn:
        return list(conn.execute(
            text("SELECT detail FROM audit_logs WHERE action = :a ORDER BY created_at"),
            {"a": action},
        ).scalars())


# AC-EXEC-MODE-01


def test_every_scope_starts_in_shadow(owner):
    assert mode_of(owner.tenant, MERCHANT) == "SHADOW"
    assert mode_of(owner.tenant, REFUND) == "SHADOW"


def test_no_broader_or_sibling_row_is_inherited(owner):
    with scoped_tx(owner.tenant) as conn:
        conn.execute(
            text(
                """
                INSERT INTO execution_mode_settings (id, tenant_id, domain_pack_id,
                    domain_pack_version, connector_id, action_capability, mode, attestation_id,
                    changed_by)
                VALUES (:id, :t, 'finops', '0.2.0', 'internal-merchant', 'merchant.payment.read',
                    'LIVE', :a, :u)
                """
            ),
            {"id": uuid4(), "t": owner.tenant, "a": uuid4(), "u": owner.principal.id},
        )
    assert mode_of(owner.tenant, MERCHANT) == "SHADOW"


# AC-EXEC-MODE-02


@pytest.mark.parametrize("body", [
    {k: v for k, v in MERCHANT.items() if k != "connector_id"},
    {k: v for k, v in MERCHANT.items() if k != "domain_pack_id"},
    {k: v for k, v in MERCHANT.items() if k != "action_capability"},
    MERCHANT | {"connector_id": "*"},
    MERCHANT | {"action_capability": "merchant.*"},
    MERCHANT | {"action_capability": "*"},
    MERCHANT | {"domain_pack_id": ""},
    MERCHANT | {"connector_id": "unknown-connector"},
    MERCHANT | {"action_capability": "merchant.payment.read"},
    MERCHANT | {"connector_id": "paypal"},
    REFUND | {"connector_id": "internal-merchant"},
    MERCHANT | {"domain_pack_id": "saas_subscription"},
    MERCHANT | {"tenant_id": str(uuid4())},
])
def test_omitted_wildcard_or_ambiguous_scopes_are_rejected(owner, body):
    response = put(owner, body, "SHADOW")
    assert response.status_code == 422, response.text
    assert audits(owner.tenant, "execution_mode.changed") == []


def test_an_unknown_mode_is_rejected(owner):
    assert put(owner, MERCHANT, "PARTIAL").status_code == 422


# Switching


def test_shadow_needs_no_attestation_and_is_audited(owner):
    response = put(owner, MERCHANT, "SHADOW")
    assert response.status_code == 200, response.text
    assert response.json()["attestation_id"] is None
    [detail] = audits(owner.tenant, "execution_mode.changed")
    assert (detail["from"], detail["to"], detail["connector_id"]) == ("SHADOW", "SHADOW",
                                                                      "internal-merchant")


def test_live_without_an_attestation_is_denied_and_audited(owner):
    response = put(owner, MERCHANT, "LIVE")
    assert response.status_code == 403
    error = response.json()["error"]
    assert (error["code"], error["policy"], error["check"]) == ("POLICY_DENIED", "POL-14",
                                                                "SIGNATURE")
    assert mode_of(owner.tenant, MERCHANT) == "SHADOW"
    [denied] = audits(owner.tenant, "execution_mode.live_denied")
    assert (denied["policy"], denied["action_capability"]) == ("POL-14",
                                                               "merchant.payment_status.correct")


def test_live_records_the_attestation_and_shadow_clears_it(owner, attested):
    response = put(owner, MERCHANT, "LIVE")
    assert response.status_code == 200, response.text
    assert response.json()["attestation_id"] == str(attested)
    with scoped_tx(owner.tenant) as conn:
        assert resolve_mode(conn, scope_of(owner.tenant, MERCHANT)).attestation_id == attested
    back = put(owner, MERCHANT, "SHADOW")
    assert back.json()["previous_mode"] == "LIVE"
    assert mode_of(owner.tenant, MERCHANT) == "SHADOW"
    assert [d["to"] for d in audits(owner.tenant, "execution_mode.changed")] == ["LIVE",
                                                                                 "SHADOW"]


def test_an_attestation_for_one_scope_does_not_switch_another(owner, attested):
    response = put(owner, REFUND, "LIVE")
    assert response.status_code == 403
    assert response.json()["error"]["check"] == "CAPABILITY_MEMBERSHIP"


# AC-EXEC-MODE-03: PB-001's merchant correction and PB-002's refund are independent (task 5.14)


@pytest.fixture
def both_attested(monkeypatch) -> dict[str, UUID]:
    """A verifier with a separate attestation per capability, as POL-14 issues them."""
    attestations = {MERCHANT["action_capability"]: uuid4(), REFUND["action_capability"]: uuid4()}

    def verify(conn, scope):
        return Verdict(True, None, attestations[scope.action_capability])

    monkeypatch.setattr(verifier, "verify", verify)
    return attestations


def test_each_playbook_runs_under_its_own_scope(owner):
    playbooks = get_playbooks()
    with scoped_tx(owner.tenant) as conn:
        pb001 = playbook_scope(conn, owner.tenant, playbooks["finops:PB-001"])
        pb002 = playbook_scope(conn, owner.tenant, playbooks["finops:PB-002"])
    assert pb001 == scope_of(owner.tenant, MERCHANT)
    assert pb002 == scope_of(owner.tenant, REFUND)


@pytest.mark.parametrize(("switched", "other"), [(MERCHANT, REFUND), (REFUND, MERCHANT)])
def test_switching_one_scope_live_leaves_the_other_unchanged(owner, both_attested, switched,
                                                             other):
    response = put(owner, switched, "LIVE")
    assert response.status_code == 200, response.text
    assert response.json()["attestation_id"] == str(
        both_attested[switched["action_capability"]])
    assert mode_of(owner.tenant, switched) == "LIVE"
    assert mode_of(owner.tenant, other) == "SHADOW"


@pytest.mark.parametrize(("returned", "other"), [(MERCHANT, REFUND), (REFUND, MERCHANT)])
def test_returning_one_scope_to_shadow_leaves_the_other_live(owner, both_attested, returned,
                                                             other):
    assert put(owner, MERCHANT, "LIVE").status_code == 200
    assert put(owner, REFUND, "LIVE").status_code == 200
    assert put(owner, returned, "SHADOW").status_code == 200
    assert mode_of(owner.tenant, returned) == "SHADOW"
    with scoped_tx(owner.tenant) as conn:
        kept = resolve_mode(conn, scope_of(owner.tenant, other))
    assert (kept.mode, kept.attestation_id) == ("LIVE", both_attested[other["action_capability"]])


def test_only_execution_mode_manage_may_switch(owner):
    operator = make_user()
    add_member(owner.tenant, operator)
    grant_set(owner.tenant, operator, "domain_operator")
    other = Member.__new__(Member)
    other.principal = owner.principal.__class__(operator, owner.principal.kind, True)
    other.tenant, other.client = owner.tenant, owner.client
    response = put(other, MERCHANT, "SHADOW")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "MISSING_PERMISSION"


def test_another_tenants_live_row_is_invisible(owner, attested):
    assert put(owner, MERCHANT, "LIVE").status_code == 200
    other = Member()
    other.activate("finops")
    assert mode_of(other.tenant, MERCHANT) == "SHADOW"


# The table itself


def test_live_needs_an_attestation_id_in_the_database(owner):
    with pytest.raises(IntegrityError), scoped_tx(owner.tenant) as conn:
        conn.execute(
            text(
                """
                INSERT INTO execution_mode_settings (id, tenant_id, domain_pack_id,
                    domain_pack_version, connector_id, action_capability, mode, changed_by)
                VALUES (:id, :t, 'finops', '0.2.0', 'paypal', 'refunds.create', 'LIVE', :u)
                """
            ),
            {"id": uuid4(), "t": owner.tenant, "u": owner.principal.id},
        )


def test_a_row_keeps_its_exact_scope(owner):
    assert put(owner, MERCHANT, "SHADOW").status_code == 200
    with pytest.raises(DBAPIError, match="exact scope"), scoped_tx(owner.tenant) as conn:
        conn.execute(text("UPDATE execution_mode_settings SET connector_id = 'paypal'"))
