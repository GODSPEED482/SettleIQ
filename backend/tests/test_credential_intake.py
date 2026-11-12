"""Write-only credential intake (spec v1.7 §11.3, AC-PP-14, OPEN-PP-03).

A tenant's Integration Admin submits its own PayPal app's credentials once. They are sealed
under a key the database never holds, resolve only in that tenant, and never come back out
through a response, an error or an audit row.
"""

import json
import os
from uuid import UUID

import pytest
from sqlalchemy import text

from app.config import get_settings
from app.connectors.paypal.client import PayPalClient, PayPalError
from app.db import scoped_tx
from app.sources import credential_store, registry
from app.sources.secrets import is_resolvable, resolve_secret
from tests.test_semantics_store import Member
from tests.test_source_credentials import Integrator, audit, credentials, stored_reference
from tests.test_sources import create_ok

pytestmark = pytest.mark.db

CLIENT_SECRET = "EKs3cr3t-value-that-must-never-leak"
VALUE = json.dumps({"client_id": "AYclient-id", "client_secret": CLIENT_SECRET})


@pytest.fixture(autouse=True)
def sealing_key(monkeypatch):
    monkeypatch.setenv("SOURCE_SECRET_KEY", os.urandom(32).hex())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def provider_checks(monkeypatch) -> list[str]:
    """Stands in for PayPal's token endpoint and records what it was asked to check."""
    checked: list[str] = []
    monkeypatch.setattr(registry, "_check_with_provider", lambda _c, value: checked.append(value))
    return checked


@pytest.fixture
def owner() -> Member:
    return Member()


@pytest.fixture
def source(owner) -> dict:
    return create_ok(owner)


def intake(source: dict) -> str:
    return f"/api/sources/{source['id']}/credential-value"


def sealed_rows(tenant: UUID) -> list:
    with scoped_tx(tenant) as conn:
        return conn.execute(
            text("SELECT source_id, nonce, ciphertext FROM source_credential_values")
        ).all()


def test_a_tenant_connects_its_own_app_without_the_value_coming_back(
    owner, source, provider_checks
):
    admin = Integrator(owner.tenant)
    response = admin.put(intake(source), {"value": VALUE})
    assert response.status_code == 200, response.text
    assert response.json()["has_secret_reference"] is True
    assert CLIENT_SECRET not in response.text and "stored:" not in response.text
    assert provider_checks == [VALUE]

    reference = stored_reference(owner.tenant, source["id"])
    assert reference == f"stored:{source['id']}"
    assert resolve_secret(owner.tenant, reference) == VALUE

    [row] = sealed_rows(owner.tenant)
    assert CLIENT_SECRET.encode() not in bytes(row.ciphertext)
    [entry] = audit(owner.tenant, "source.credential.")
    assert entry.action == "source.credential.created"
    assert json.loads(entry.detail) == {"source_id": source["id"]}

    listed = owner.request("GET", "/api/sources").text
    assert CLIENT_SECRET not in listed and "stored:" not in listed


def test_rotation_stops_the_old_value_being_used(owner, source, provider_checks):
    admin = Integrator(owner.tenant)
    assert admin.put(intake(source), {"value": VALUE}).status_code == 200
    rotated = VALUE.replace(CLIENT_SECRET, "rotated-secret")
    assert admin.put(intake(source), {"value": rotated}).status_code == 200
    reference = stored_reference(owner.tenant, source["id"])
    assert resolve_secret(owner.tenant, reference) == rotated
    assert len(sealed_rows(owner.tenant)) == 1
    assert [row.action for row in audit(owner.tenant, "source.credential.")] == [
        "source.credential.created", "source.credential.rotated",
    ]


def test_moving_to_another_reference_revokes_the_stored_value(
    owner, source, provider_checks, monkeypatch
):
    monkeypatch.setenv("SOURCE_SECRET_PAYPAL_SANDBOX", "client:secret")
    admin = Integrator(owner.tenant)
    assert admin.put(intake(source), {"value": VALUE}).status_code == 200
    moved = admin.put(credentials(source), {"secret_reference": "env:SOURCE_SECRET_PAYPAL_SANDBOX"})
    assert moved.status_code == 200, moved.text
    assert sealed_rows(owner.tenant) == []
    assert not is_resolvable(owner.tenant, f"stored:{source['id']}")


def test_inherited_source_keys_cannot_use_the_intake(owner, source, provider_checks):
    response = owner.request("PUT", intake(source), json={"value": VALUE})
    assert response.status_code == 403
    assert response.json()["error"]["permission"] == "source.credential.manage"
    assert CLIENT_SECRET not in response.text
    assert provider_checks == []
    assert stored_reference(owner.tenant, source["id"]) is None
    assert sealed_rows(owner.tenant) == []


def test_credentials_paypal_refuses_store_nothing(owner, source, monkeypatch):
    def refuse(self):
        raise PayPalError(f"OAuth token request failed: {CLIENT_SECRET}", 401)

    monkeypatch.setattr(PayPalClient, "get_token", refuse)
    for value in (VALUE, "no-separator", '{"client_id": "only"}'):
        response = Integrator(owner.tenant).put(intake(source), {"value": value})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "CREDENTIAL_REJECTED"
        assert CLIENT_SECRET not in response.text and "only" not in response.text
    assert stored_reference(owner.tenant, source["id"]) is None
    assert sealed_rows(owner.tenant) == []
    assert audit(owner.tenant, "source.credential.") == []


@pytest.mark.parametrize(
    "body",
    [{}, {"value": ""}, {"value": "   "}, {"value": VALUE, "secret_reference": "env:X"}],
)
def test_malformed_bodies_echo_nothing(owner, source, provider_checks, body):
    response = Integrator(owner.tenant).put(intake(source), body)
    assert response.status_code == 422
    assert CLIENT_SECRET not in response.text
    assert provider_checks == []
    assert sealed_rows(owner.tenant) == []


def test_the_intake_is_closed_without_a_sealing_key(owner, source, provider_checks, monkeypatch):
    monkeypatch.setenv("SOURCE_SECRET_KEY", "")
    get_settings.cache_clear()
    response = Integrator(owner.tenant).put(intake(source), {"value": VALUE})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CREDENTIAL_STORE_UNAVAILABLE"
    assert provider_checks == []


def test_a_source_that_takes_no_credentials_is_refused(owner, provider_checks):
    csv = create_ok(owner, connector_id="tenant-csv", display_name="Bank CSV", config={})
    response = Integrator(owner.tenant).put(intake(csv), {"value": VALUE})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CREDENTIALS_UNSUPPORTED"


@pytest.mark.parametrize("connector_id", ["merchant-postgres", "internal-merchant"])
def test_a_source_the_server_cannot_check_takes_no_typed_credential(
    owner, provider_checks, connector_id
):
    database = create_ok(
        owner, connector_id=connector_id, display_name="Merchant DB",
        config={"merchant_id": "M-A"} if connector_id == "internal-merchant" else {},
    )
    response = Integrator(owner.tenant).put(intake(database), {"value": VALUE})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CREDENTIALS_UNSUPPORTED"
    assert "managed reference" in response.json()["error"]["message"]
    assert provider_checks == []
    assert sealed_rows(owner.tenant) == []
    assert stored_reference(owner.tenant, database["id"]) is None


def test_another_tenant_can_neither_write_nor_reach_the_value(owner, source, provider_checks):
    assert Integrator(owner.tenant).put(intake(source), {"value": VALUE}).status_code == 200
    other = Member()
    stranger = Integrator(other.tenant)
    assert stranger.put(intake(source), {"value": "theirs:value"}).status_code == 404
    assert resolve_secret(owner.tenant, f"stored:{source['id']}") == VALUE

    their_source = create_ok(other)
    borrowed = {"secret_reference": f"stored:{source['id']}"}
    refused = stranger.put(credentials(their_source), borrowed)
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "SECRET_UNRESOLVED"
    assert not is_resolvable(other.tenant, f"stored:{source['id']}")


def test_a_sealed_value_opens_only_for_its_own_tenant_and_source(owner, source, provider_checks):
    assert Integrator(owner.tenant).put(intake(source), {"value": VALUE}).status_code == 200
    [row] = sealed_rows(owner.tenant)
    other = Member()
    their_source = create_ok(other)
    with scoped_tx(other.tenant) as conn:
        conn.execute(
            text(
                "INSERT INTO source_credential_values "
                "(tenant_id, source_id, nonce, ciphertext, key_fingerprint) "
                "VALUES (:t, :s, :n, :c, :f)"
            ),
            {
                "t": other.tenant, "s": UUID(their_source["id"]), "n": bytes(row.nonce),
                "c": bytes(row.ciphertext),
                "f": credential_store._fingerprint(credential_store._key()),
            },
        )
    assert credential_store.stored_value(other.tenant, UUID(their_source["id"])) == ""


def test_a_changed_key_fails_closed(owner, source, provider_checks, monkeypatch):
    assert Integrator(owner.tenant).put(intake(source), {"value": VALUE}).status_code == 200
    monkeypatch.setenv("SOURCE_SECRET_KEY", os.urandom(32).hex())
    get_settings.cache_clear()
    assert not is_resolvable(owner.tenant, f"stored:{source['id']}")
