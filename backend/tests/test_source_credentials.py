"""Secret references and webhook registration (task 4.5, spec 11.3, AC-PP-10).

Both routes need their own key. Holding every other source key, as the tenant owner does through
the sets it includes, is not enough.
"""

import json
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from app.config import get_settings
from app.db import scoped_tx
from app.sources import registry
from tests.support import Actor, add_member, grant_set
from tests.test_semantics_store import Member
from tests.test_sources import create_ok

pytestmark = pytest.mark.db

SECRET = "client-id:client-secret-value"
REFERENCE = "env:SOURCE_SECRET_PAYPAL_SANDBOX"
EVENTS = ["PAYMENT.CAPTURE.COMPLETED", "PAYMENT.CAPTURE.REFUNDED"]


@pytest.fixture(autouse=True)
def paypal_secret(monkeypatch):
    monkeypatch.setenv("SOURCE_SECRET_PAYPAL_SANDBOX", SECRET)
    monkeypatch.setenv("SOURCE_SECRET_PAYPAL_ROTATED", SECRET + "-2")
    monkeypatch.delenv("SOURCE_SECRET_UNSET", raising=False)


class Integrator(Actor):
    """A member granted only the integration set, acting in one tenant."""

    def __init__(self, tenant: UUID) -> None:
        super().__init__()
        self.tenant = tenant
        add_member(tenant, self.id)
        grant_set(tenant, self.id, "integration_admin")

    def put(self, path: str, body: dict):
        return self.request("PUT", path, tenant=self.tenant, json=body)


@pytest.fixture
def owner() -> Member:
    return Member()


@pytest.fixture
def source(owner) -> dict:
    return create_ok(owner)


def credentials(source: dict) -> str:
    return f"/api/sources/{source['id']}/credentials"


def webhooks(source: dict) -> str:
    return f"/api/sources/{source['id']}/webhooks"


def audit(tenant: UUID, action_prefix: str) -> list:
    with scoped_tx(tenant) as conn:
        return conn.execute(
            text(
                "SELECT action, detail::text AS detail FROM audit_logs "
                "WHERE action LIKE :prefix ORDER BY created_at, id"
            ),
            {"prefix": f"{action_prefix}%"},
        ).all()


def stored_reference(tenant: UUID, source_id: str) -> str | None:
    with scoped_tx(tenant) as conn:
        return conn.execute(
            text("SELECT secret_reference FROM data_sources WHERE id = :id"),
            {"id": UUID(source_id)},
        ).scalar_one()


# AC-PP-10: the inherited side is refused, the granted side succeeds


def test_inherited_source_keys_cannot_manage_credentials_or_webhooks(owner, source):
    response = owner.request("GET", "/api/me/permissions", params={"tenant_id": owner.tenant})
    held = {item["key"] for item in response.json()["permissions"]}  # effective, from data
    assert {"source.create", "source.update", "source.discover"} <= held
    assert not {"source.credential.manage", "source.webhook.manage"} & held

    for path, body in (
        (credentials(source), {"secret_reference": REFERENCE}),
        (webhooks(source), {"webhook_id": "WH-1", "event_types": EVENTS}),
    ):
        response = owner.request("PUT", path, json=body)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "MISSING_PERMISSION"
    assert stored_reference(owner.tenant, source["id"]) is None
    assert audit(owner.tenant, "source.credential.") == []
    assert audit(owner.tenant, "source.webhook.") == []


def test_credential_reference_is_stored_and_rotated_without_echoing_it(owner, source):
    admin = Integrator(owner.tenant)
    created = admin.put(credentials(source), {"secret_reference": REFERENCE})
    assert created.status_code == 200, created.text
    assert created.json()["has_secret_reference"] is True
    assert REFERENCE not in created.text and SECRET not in created.text

    rotation = "env:SOURCE_SECRET_PAYPAL_ROTATED"
    rotated = admin.put(credentials(source), {"secret_reference": rotation})
    assert rotated.status_code == 200, rotated.text
    assert stored_reference(owner.tenant, source["id"]) == rotation

    rows = audit(owner.tenant, "source.credential.")
    assert [row.action for row in rows] == [
        "source.credential.created", "source.credential.rotated",
    ]
    for row in rows:
        assert json.loads(row.detail) == {"source_id": source["id"]}
        assert "SOURCE_SECRET" not in row.detail and SECRET not in row.detail

    listed = owner.request("GET", "/api/sources").text
    assert "SOURCE_SECRET" not in listed and SECRET not in listed


@pytest.mark.parametrize(
    "body",
    [
        {"secret_reference": REFERENCE, "secret_value": SECRET},
        {"value": SECRET},
        {"secret_reference": ""},
    ],
)
def test_only_a_reference_is_accepted(owner, source, body):
    response = Integrator(owner.tenant).put(credentials(source), body)
    assert response.status_code == 422
    assert SECRET not in response.text
    assert stored_reference(owner.tenant, source["id"]) is None


@pytest.mark.parametrize(
    "reference", ["env:SOURCE_SECRET_UNSET", "env:DATABASE_URL", "managed:NOT_RENDERED", SECRET]
)
def test_unresolvable_reference_is_refused(owner, source, reference):
    response = Integrator(owner.tenant).put(credentials(source), {"secret_reference": reference})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SECRET_UNRESOLVED"
    assert reference not in response.text
    assert stored_reference(owner.tenant, source["id"]) is None
    assert audit(owner.tenant, "source.credential.") == []


def test_another_tenants_source_is_not_found(owner, source):
    stranger = Integrator(Member().tenant)
    for path, body in (
        (credentials(source), {"secret_reference": REFERENCE}),
        (webhooks(source), {"webhook_id": "WH-1", "event_types": EVENTS}),
    ):
        assert stranger.put(path, body).status_code == 404
    unknown = {"id": str(uuid4())}
    assert stranger.put(credentials(unknown), {"secret_reference": REFERENCE}).status_code == 404
    assert stored_reference(owner.tenant, source["id"]) is None


@pytest.fixture
def managed_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SOURCE_SECRET_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def test_a_reference_names_a_secret_in_its_own_tenant_only(owner, source, managed_dir):
    (managed_dir / str(owner.tenant)).mkdir()
    (managed_dir / str(owner.tenant) / "PAYPAL_SANDBOX").write_text(SECRET, encoding="utf-8")
    reference = {"secret_reference": "managed:PAYPAL_SANDBOX"}

    other = Member()
    other_source = create_ok(other)
    stranger = Integrator(other.tenant)
    refused = stranger.put(credentials(other_source), reference)
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "SECRET_UNRESOLVED"
    assert stored_reference(other.tenant, other_source["id"]) is None

    # Even stored without the route's check, the reference resolves in the source's tenant only.
    with scoped_tx(other.tenant) as conn:
        conn.execute(
            text("UPDATE data_sources SET secret_reference = :r WHERE id = :id"),
            {"r": reference["secret_reference"], "id": UUID(other_source["id"])},
        )
    sync = stranger.request("POST", f"/api/sources/{other_source['id']}/sync", tenant=other.tenant)
    assert sync.status_code == 409, sync.text
    assert sync.json()["error"]["code"] == "SECRET_UNRESOLVED"

    accepted = Integrator(owner.tenant).put(credentials(source), reference)
    assert accepted.status_code == 200, accepted.text


# Webhook registration


def test_webhook_registration_reaches_the_trusted_lookup(owner, source):
    admin = Integrator(owner.tenant)
    response = admin.put(webhooks(source), {"webhook_id": "WH-SANDBOX-9", "event_types": EVENTS})
    assert response.status_code == 200, response.text

    trusted = registry.webhook_source(UUID(source["id"]))
    assert trusted["tenant_id"] == owner.tenant
    assert trusted["config"]["webhook"] == {"webhook_id": "WH-SANDBOX-9", "event_types": EVENTS}

    [row] = audit(owner.tenant, "source.webhook.")
    assert row.action == "source.webhook.configured"
    assert json.loads(row.detail)["webhook_id"] == "WH-SANDBOX-9"


@pytest.mark.parametrize(
    "event_types",
    [["PAYMENT.CAPTURE.COMPLETED", "CHECKOUT.ORDER.APPROVED"], [], EVENTS[:1] * 2],
)
def test_webhook_event_types_follow_the_pinned_contract(owner, source, event_types):
    response = Integrator(owner.tenant).put(
        webhooks(source), {"webhook_id": "WH-1", "event_types": event_types}
    )
    assert response.status_code == 422
    assert "webhook" not in registry.webhook_source(UUID(source["id"]))["config"]


def test_connector_without_webhooks_is_refused(owner):
    csv = create_ok(owner, connector_id="tenant-csv", display_name="Bank files", config={})
    response = Integrator(owner.tenant).put(
        webhooks(csv), {"webhook_id": "WH-1", "event_types": EVENTS}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "WEBHOOKS_UNSUPPORTED"
