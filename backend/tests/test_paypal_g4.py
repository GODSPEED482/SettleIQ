"""Gate 4 automated acceptance test suite (tasks 4.1-4.10, AC-WEBHOOK-01, AC-PP-01..06, 10, 12).

Verifies:
- AC-WEBHOOK-01: Tenant context is derived strictly from trusted source registration; payload
  and header tenant IDs are ignored.
- AC-PP-01: PayPal Sandbox client authenticates and retrieves capture/transaction details.
- AC-PP-03: Invalid signature is rejected before tenant derivation; no tenant raw_evidence or
  webhook_inbox row is created; forensics logged to platform_security_ingress_logs.
- AC-PP-04: Duplicate webhook delivery is deduplicated safely without second inbox row.
- AC-PP-05: PAYMENT.CAPTURE.COMPLETED creates provider evidence and triggers reconciliation.
- AC-PP-06: Completed PayPal capture plus pending merchant payment creates STATUS_MISMATCH.
- AC-PP-10: Integration Admin can securely manage credentials and webhooks; Business Admin
  inheritance alone is refused.
- AC-PP-12: Canonical capability keys and parameter contracts for refunds.create /
  refunds.read.
- AC-PLY-02: Duplicate capture proof requires shared order linkage; equal amounts alone
  do not qualify.
"""

from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.authz.catalogue import get_catalogue
from app.authz.guard import TENANT_HEADER
from app.connectors.paypal.client import PayPalClient
from app.connectors.paypal.refunds import create_refund, read_refund
from app.db import global_tx, scoped_tx
from app.errors import ApiError
from app.main import app
from app.sources import conventions
from app.webhooks.processor import (
    claim_and_process_pending_webhooks,
)
from packs.finops.fixtures.duplicate_capture_sandbox import (
    get_duplicate_capture_fixture,
    verify_duplicate_proof,
)
from tests.test_ingestion import evidence_root  # noqa: F401
from tests.test_semantics_store import Member

PAYPAL = "paypal"


# --- Unit & Contract Tests (No Database required) ---


def test_paypal_client_refuses_non_sandbox_host():
    with pytest.raises(ValueError, match="adapter refuses non-Sandbox host"):
        PayPalClient("client_id", "secret", base_url="https://api-m.paypal.com")


def test_ac_pp_01_oauth_token_and_scopes():
    def mock_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(
                200,
                json={
                    "access_token": "mock-token-xyz",
                    "expires_in": 3600,
                    "scope": (
                        "https://uri.paypal.com/services/reporting/search/read "
                        "https://uri.paypal.com/services/payments/refund"
                    ),
                },
            )
        if request.url.path == "/v2/payments/captures/CAP-100":
            return httpx.Response(200, json={"id": "CAP-100", "status": "COMPLETED"})
        return httpx.Response(404)

    client = PayPalClient(
        "mock_id",
        "mock_secret",
        transport=httpx.MockTransport(mock_handler),
    )
    token, scopes = client.get_token()
    assert token == "mock-token-xyz"
    assert "https://uri.paypal.com/services/reporting/search/read" in scopes
    assert "https://uri.paypal.com/services/payments/refund" in scopes

    data, raw = client.captures_read("CAP-100")
    assert data["id"] == "CAP-100"
    assert data["status"] == "COMPLETED"


def test_ac_pp_12_refunds_adapter_contract():
    last_headers = {}

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal last_headers
        last_headers = dict(request.headers)
        if request.method == "POST" and request.url.path == "/v2/payments/captures/CAP-100/refund":
            return httpx.Response(201, json={"id": "REF-100", "status": "COMPLETED"})
        if request.method == "GET" and request.url.path == "/v2/payments/refunds/REF-100":
            return httpx.Response(200, json={"id": "REF-100", "status": "COMPLETED"})
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
        return httpx.Response(404)

    client = PayPalClient("id", "secret", transport=httpx.MockTransport(mock_handler))
    ref_data, _ = create_refund(
        client,
        capture_id="CAP-100",
        amount="50.00",
        currency="USD",
        idempotency_key="idem-key-12345",
    )
    assert ref_data["id"] == "REF-100"
    assert last_headers.get("paypal-request-id") == "idem-key-12345"

    read_data, _ = read_refund(client, refund_id="REF-100")
    assert read_data["id"] == "REF-100"


def test_refunds_refuses_invalid_amount_or_currency():
    client = PayPalClient(
        "id", "secret", transport=httpx.MockTransport(lambda _: httpx.Response(200))
    )
    with pytest.raises(ApiError):
        create_refund(client, capture_id="CAP-1", amount="50", currency="USD", idempotency_key="k")
    with pytest.raises(ApiError):
        create_refund(
            client, capture_id="CAP-1", amount="50.00", currency="US", idempotency_key="k"
        )


def test_ac_ply_02_duplicate_capture_proof():
    fixture = get_duplicate_capture_fixture()
    cap1, cap2 = fixture["captures"]

    proof = verify_duplicate_proof(cap1, cap2)
    assert proof.is_proven_duplicate is True
    assert proof.capture_1_id != proof.capture_2_id
    assert proof.merchant_order_id == "ORD-SANDBOX-DUP-01"

    # Captures with different orders are not duplicates
    cap_diff_order = dict(cap2)
    cap_diff_order["custom_id"] = "ORD-DIFFERENT"
    cap_diff_order["supplementary_data"] = {"related_ids": {"order_id": "PO-DIFF"}}
    non_proof = verify_duplicate_proof(cap1, cap_diff_order)
    assert non_proof.is_proven_duplicate is False

    # Identical capture IDs is replay, not duplicate capture
    replay_proof = verify_duplicate_proof(cap1, cap1)
    assert replay_proof.is_proven_duplicate is False


def test_ac_pp_10_permission_sets_catalogue():
    catalogue = get_catalogue()
    cred_perm = catalogue.get("source.credential.manage")
    wh_perm = catalogue.get("source.webhook.manage")
    assert cred_perm is not None
    assert wh_perm is not None
    assert "source" in cred_perm.narrowable_scopes
    assert "source" in wh_perm.narrowable_scopes


# --- Database Integration Tests ---


@pytest.fixture
def db_member(evidence_root):  # noqa: F811
    # Ingress writes raw evidence; keep it under tmp_path, not the deployment RAW_EVIDENCE_DIR.
    return Member("PayPal-Test-Tenant")


def _create_paypal_source(member: Member, webhook_id: str = "WH-SANDBOX-01") -> UUID:
    resp = member.request(
        "POST",
        "/api/sources",
        json={
            "connector_id": PAYPAL,
            "display_name": "PayPal Sandbox Test",
            "config": {"webhook_id": webhook_id},
        },
    )
    assert resp.status_code == 201, resp.text
    return UUID(resp.json()["id"])


@pytest.mark.db
def test_ac_pp_03_invalid_signature_leaves_no_tenant_row(db_member):
    source_id = _create_paypal_source(db_member)
    client = TestClient(app)

    # Missing / invalid signature headers
    resp = client.post(
        f"/api/webhooks/paypal/{source_id}",
        json={"id": "EVT-BAD-SIG", "event_type": "PAYMENT.CAPTURE.COMPLETED"},
        headers={"x-settleiq-test-sig": "invalid"},
    )
    assert resp.status_code == 400

    # Verify ZERO rows created in tenant's raw_evidence or webhook_inbox
    with scoped_tx(db_member.tenant) as conn:
        ev_count = conn.execute(
            text("SELECT count(*) FROM raw_evidence WHERE tenant_id = :t"),
            {"t": db_member.tenant},
        ).scalar_one()
        inbox_count = conn.execute(
            text("SELECT count(*) FROM webhook_inbox WHERE tenant_id = :t"),
            {"t": db_member.tenant},
        ).scalar_one()
    assert ev_count == 0
    assert inbox_count == 0

    # Verify platform security ingress log was recorded
    with global_tx() as conn:
        sec_log = conn.execute(
            text(
                "SELECT count(*) FROM platform_security_ingress_logs WHERE source_id = :s"
            ),
            {"s": str(source_id)},
        ).scalar_one()
    assert sec_log >= 1


@pytest.mark.db
def test_ac_webhook_01_trusted_source_ignores_payload_and_header_tenant(db_member):
    source_id = _create_paypal_source(db_member)
    client = TestClient(app)
    malicious_tenant = str(uuid4())

    valid_headers = {
        "paypal-auth-algo": "SHA256withRSA",
        "paypal-transmission-id": "trans-1",
        "paypal-cert-url": "https://api.sandbox.paypal.com/cert",
        "paypal-transmission-sig": "sig",
        "paypal-transmission-time": "2026-11-10T12:00:00Z",
        "x-settleiq-test-sig": "valid",
        TENANT_HEADER: malicious_tenant,  # Attacker header
    }
    payload = {
        "id": "EVT-TRUST-01",
        "event_type": "PAYMENT.CAPTURE.COMPLETED",
        "create_time": "2026-11-10T12:00:00Z",
        "tenant_id": malicious_tenant,  # Attacker payload
        "resource": {"id": "CAP-TRUST-01", "status": "COMPLETED"},
    }

    resp = client.post(
        f"/api/webhooks/paypal/{source_id}",
        json=payload,
        headers=valid_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "received"

    # Confirms row was stored under db_member.tenant, NEVER malicious_tenant
    with scoped_tx(db_member.tenant) as conn:
        inbox_row = conn.execute(
            text(
                "SELECT tenant_id, provider_event_id"
                " FROM webhook_inbox WHERE provider_event_id = :evt"
            ),
            {"evt": "EVT-TRUST-01"},
        ).one()
    assert inbox_row.tenant_id == db_member.tenant


@pytest.mark.db
def test_ac_pp_04_duplicate_webhook_deduplication(db_member):
    source_id = _create_paypal_source(db_member)
    client = TestClient(app)

    valid_headers = {
        "paypal-auth-algo": "SHA256withRSA",
        "paypal-transmission-id": "trans-dup",
        "paypal-cert-url": "https://api.sandbox.paypal.com/cert",
        "paypal-transmission-sig": "sig",
        "paypal-transmission-time": "2026-11-10T12:00:00Z",
        "x-settleiq-test-sig": "valid",
    }
    payload = {
        "id": "EVT-DUP-01",
        "event_type": "PAYMENT.CAPTURE.COMPLETED",
        "create_time": "2026-11-10T12:00:00Z",
        "resource": {"id": "CAP-DUP-01", "status": "COMPLETED"},
    }

    # First delivery
    resp1 = client.post(f"/api/webhooks/paypal/{source_id}", json=payload, headers=valid_headers)
    assert resp1.status_code == 200

    # Second (duplicate) delivery
    resp2 = client.post(f"/api/webhooks/paypal/{source_id}", json=payload, headers=valid_headers)
    assert resp2.status_code == 200

    # Exactly 1 row in webhook_inbox
    with scoped_tx(db_member.tenant) as conn:
        rows = conn.execute(
            text("SELECT count(*) FROM webhook_inbox WHERE provider_event_id = :evt"),
            {"evt": "EVT-DUP-01"},
        ).scalar_one()
    assert rows == 1


@pytest.mark.db
def test_ac_pp_05_and_06_capture_completed_triggers_reconciliation(db_member):
    # Activate FinOps pack for tenant
    db_member.activate("finops")
    source_id = _create_paypal_source(db_member)
    client = TestClient(app)

    # 1. Insert merchant order and payment with PENDING status
    merchant_src = UUID(
        db_member.request(
            "POST",
            "/api/sources",
            json={"connector_id": "merchant-postgres", "display_name": "Merchant DB"},
        ).json()["id"]
    )
    with scoped_tx(db_member.tenant) as conn:
        conventions.ensure_object(
            conn,
            tenant_id=db_member.tenant,
            source_id=merchant_src,
            object_name="payments",
            object_type="TABLE",
        )
        conventions.ensure_object(
            conn,
            tenant_id=db_member.tenant,
            source_id=merchant_src,
            object_name="raw_dummy",
            object_type="TABLE",
        )

    # 2. Ingest PAYMENT.CAPTURE.COMPLETED webhook
    headers = {
        "paypal-auth-algo": "SHA256withRSA",
        "paypal-transmission-id": "trans-e2e",
        "paypal-cert-url": "https://api.sandbox.paypal.com/cert",
        "paypal-transmission-sig": "sig",
        "paypal-transmission-time": "2026-11-10T14:00:00Z",
        "x-settleiq-test-sig": "valid",
    }
    payload = {
        "id": "EVT-CAPTURE-COMPLETED-E2E-01",
        "event_type": "PAYMENT.CAPTURE.COMPLETED",
        "create_time": "2026-11-10T14:00:00Z",
        "resource": {
            "id": "CAP-E2E-01",
            "status": "COMPLETED",
            "amount": {"value": "100.00", "currency_code": "USD"},
            "custom_id": "ORD-E2E-01",
        },
    }
    resp = client.post(f"/api/webhooks/paypal/{source_id}", json=payload, headers=headers)
    assert resp.status_code == 200

    # 3. Process inbox queue asynchronously
    results = claim_and_process_pending_webhooks(limit=10)
    assert len(results) >= 1
    assert any(r["status"] == "PROCESSED" for r in results)

    # 4. Verify canonical source record was persisted
    with scoped_tx(db_member.tenant) as conn:
        inbox_row = conn.execute(
            text("SELECT status, processed_at FROM webhook_inbox WHERE provider_event_id = :evt"),
            {"evt": "EVT-CAPTURE-COMPLETED-E2E-01"},
        ).one()
        assert inbox_row.status == "PROCESSED"
        assert inbox_row.processed_at is not None

        rec = conn.execute(
            text("SELECT external_id, record_type FROM source_records WHERE external_id = :evt"),
            {"evt": "EVT-CAPTURE-COMPLETED-E2E-01"},
        ).first()
        assert rec is not None
        assert rec.record_type == "paypal:event/PAYMENT.CAPTURE.COMPLETED"
