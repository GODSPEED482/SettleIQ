"""Webhook failure tests: invalid signature, duplicate and out-of-order events (task 4.8).

Spec 10.4, POL-15, AC-PP-03, AC-PP-04, NFR-011. Every delivery is signed with PayPal's
algorithm (tests/paypal_signing.py) and goes through the public route, so the code under test
is the production verifier.

- Invalid signature: each forgery is a 400, writes no tenant `raw_evidence` or
  `webhook_inbox` row, and leaves one platform forensics row.
- Duplicates: a redelivered event, before or after processing, is one inbox row, one source
  record and no second domain run.
- Out of order: the outcome does not depend on arrival order (spec 5.6), every event keeps its
  provider time, and an event that arrives before the record it reconciles against is not lost.
- Retry: a failing event is retried up to its limit, then dead-lettered for review.
"""

import base64
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.connectors.manifest import load_connector_manifests
from app.db import global_tx, scoped_tx
from app.main import app
from app.webhooks import processor
from tests.paypal_signing import encode, foreign_signer, register_webhook
from tests.test_ingestion import evidence_root  # noqa: F401
from tests.test_paypal_mappings import PayPalTenant, paypal_capture, pending
from tests.test_semantics_store import Member

pytestmark = pytest.mark.db

WEBHOOK_ID = "WH-FAILURES-01"
client = TestClient(app)


def url(source_id: UUID | str) -> str:
    return f"/api/webhooks/paypal/{source_id}"


def event(event_id: str, event_type: str = "PAYMENT.CAPTURE.COMPLETED",
          resource: dict[str, Any] | None = None,
          create_time: str = "2026-03-01T00:00:00Z") -> dict[str, Any]:
    return {
        "id": event_id,
        "event_type": event_type,
        "create_time": create_time,
        "resource_type": "capture",
        "resource": resource or paypal_capture(f"CAP-{event_id}", f"ORD-{event_id}"),
    }


def paypal_source(member: Member, webhook_id: str | None = WEBHOOK_ID) -> UUID:
    response = member.request(
        "POST", "/api/sources", json={"connector_id": "paypal", "display_name": "PayPal"}
    )
    assert response.status_code == 201, response.text
    source_id = UUID(response.json()["id"])
    if webhook_id is not None:
        register_webhook(member, source_id, webhook_id)
    return source_id


def tenant_rows(tenant: UUID) -> tuple[int, int]:
    """(raw_evidence, webhook_inbox) rows the tenant holds."""
    with scoped_tx(tenant) as conn:
        return (
            conn.execute(text("SELECT count(*) FROM raw_evidence")).scalar_one(),
            conn.execute(text("SELECT count(*) FROM webhook_inbox")).scalar_one(),
        )


def forensics(source_id: UUID | str) -> int:
    with global_tx() as conn:
        return conn.execute(
            text("SELECT count(*) FROM platform_security_ingress_logs WHERE source_id = :s"),
            {"s": str(source_id)},
        ).scalar_one()


def inbox(tenant: UUID, event_id: str):
    with scoped_tx(tenant) as conn:
        return conn.execute(
            text(
                "SELECT id, status, attempts, max_attempts, last_error, processed_at "
                "FROM webhook_inbox WHERE provider_event_id = :e"
            ),
            {"e": event_id},
        ).all()


def records_for(tenant: UUID, event_id: str) -> int:
    with scoped_tx(tenant) as conn:
        return conn.execute(
            text("SELECT count(*) FROM source_records WHERE external_id = :e"), {"e": event_id}
        ).scalar_one()


def process() -> list[dict[str, Any]]:
    """Drain the queue. Rows other tests left behind are processed too, so assert on yours."""
    return processor.claim_and_process_pending_webhooks(limit=1000)


@pytest.fixture
def member(evidence_root, paypal_signer):  # noqa: F811
    return Member("Webhook failures")


# Invalid signature (AC-PP-03): each forgery returns (body sent, headers sent).


def _no_transmission_headers(signer, body):
    return body, {"content-type": "application/json"}


def _body_changed_after_signing(signer, body):
    return body.replace(b"100.00", b"999.00"), signer.headers(body, WEBHOOK_ID)


def _signed_for_another_webhook(signer, body):
    return body, signer.headers(body, "WH-SOMEONE-ELSE")


def _former_test_bypass_header(signer, body):
    headers = signer.headers(body, WEBHOOK_ID)
    junk = base64.b64encode(b"not a signature").decode()
    return body, headers | {"paypal-transmission-sig": junk, "x-settleiq-test-sig": "valid"}


def _key_paypal_did_not_issue(signer, body):
    return body, foreign_signer().headers(body, WEBHOOK_ID)


def _certificate_off_paypal(signer, body):
    return body, signer.headers(body, WEBHOOK_ID, cert_url="https://certs.example.com/pp.pem")


def _certificate_over_http(signer, body):
    cert_url = "http://api.sandbox.paypal.com/v1/notifications/certs/CERT-SETTLEIQ-TEST"
    return body, signer.headers(body, WEBHOOK_ID, cert_url=cert_url)


def _unsupported_algorithm(signer, body):
    return body, signer.headers(body, WEBHOOK_ID) | {"paypal-auth-algo": "SHA1withRSA"}


def _signature_not_base64(signer, body):
    return body, signer.headers(body, WEBHOOK_ID) | {"paypal-transmission-sig": "%%%"}


FORGERIES = [
    _no_transmission_headers,
    _body_changed_after_signing,
    _signed_for_another_webhook,
    _former_test_bypass_header,
    _key_paypal_did_not_issue,
    _certificate_off_paypal,
    _certificate_over_http,
    _unsupported_algorithm,
    _signature_not_base64,
]


@pytest.mark.parametrize("forge", FORGERIES, ids=lambda forge: forge.__name__.strip("_"))
def test_invalid_signature_writes_nothing_tenant_owned(member, paypal_signer, forge):
    source = paypal_source(member)
    before = forensics(source)
    body, headers = forge(paypal_signer, encode(event("EVT-FORGED")))

    response = client.post(url(source), content=body, headers=headers)

    assert response.status_code == 400
    assert tenant_rows(member.tenant) == (0, 0)
    assert forensics(source) == before + 1


def test_the_genuine_signature_is_accepted(member, paypal_signer):
    # The control for the forgeries above: same source, same event, correctly signed.
    source = paypal_source(member)
    response = paypal_signer.deliver(client, source, event("EVT-GENUINE"), WEBHOOK_ID)
    assert response.status_code == 200, response.text
    assert tenant_rows(member.tenant) == (1, 1)


def test_unknown_source_writes_nothing(member, paypal_signer):
    unknown = uuid4()
    response = paypal_signer.deliver(client, unknown, event("EVT-UNKNOWN"), WEBHOOK_ID)
    assert response.status_code == 400
    assert forensics(unknown) == 1


def test_source_without_a_registered_webhook_writes_nothing(member, paypal_signer):
    source = paypal_source(member, webhook_id=None)
    response = paypal_signer.deliver(client, source, event("EVT-UNREGISTERED"), WEBHOOK_ID)
    assert response.status_code == 400
    assert tenant_rows(member.tenant) == (0, 0)
    assert forensics(source) == 1


def test_a_non_paypal_source_writes_nothing(member, paypal_signer):
    created = member.request(
        "POST", "/api/sources", json={"connector_id": "tenant-csv", "display_name": "Bank"}
    )
    source = created.json()["id"]
    response = paypal_signer.deliver(client, source, event("EVT-CSV"), WEBHOOK_ID)
    assert response.status_code == 400
    assert tenant_rows(member.tenant) == (0, 0)


def test_a_signature_for_another_tenants_webhook_is_refused(member, paypal_signer):
    other = Member("Other tenant")
    theirs = paypal_source(other, webhook_id="WH-OTHER-TENANT")
    ours = paypal_source(member)

    # Correctly signed for the other tenant's webhook, delivered to our source.
    body = encode(event("EVT-CROSS"))
    headers = paypal_signer.headers(body, "WH-OTHER-TENANT")
    assert client.post(url(ours), content=body, headers=headers).status_code == 400
    assert tenant_rows(member.tenant) == (0, 0)
    assert tenant_rows(other.tenant) == (0, 0)

    # The same delivery to the right source lands in the other tenant only.
    assert client.post(url(theirs), content=body, headers=headers).status_code == 200
    assert tenant_rows(other.tenant) == (1, 1)
    assert tenant_rows(member.tenant) == (0, 0)


# Duplicates (AC-PP-04)


def test_redelivery_before_processing_is_one_inbox_row(member, paypal_signer):
    source = paypal_source(member)
    for _ in range(3):  # PayPal retries until it sees a 2xx; each has a new transmission ID
        response = paypal_signer.deliver(client, source, event("EVT-DUP-EARLY"), WEBHOOK_ID)
        assert response.status_code == 200
        assert response.json()["event_id"] == "EVT-DUP-EARLY"
    assert len(inbox(member.tenant, "EVT-DUP-EARLY")) == 1


def test_redelivery_after_processing_changes_nothing(member, paypal_signer):
    source = paypal_source(member)
    paypal_signer.deliver(client, source, event("EVT-DUP-LATE"), WEBHOOK_ID)
    process()
    [first] = inbox(member.tenant, "EVT-DUP-LATE")
    assert first.status == "PROCESSED"

    response = paypal_signer.deliver(client, source, event("EVT-DUP-LATE"), WEBHOOK_ID)
    assert response.status_code == 200
    process()

    [again] = inbox(member.tenant, "EVT-DUP-LATE")
    assert (again.id, again.status, again.attempts, again.processed_at) == (
        first.id, "PROCESSED", first.attempts, first.processed_at
    )
    assert records_for(member.tenant, "EVT-DUP-LATE") == 1


# Retry and dead letter (spec 10.4, NFR-011)


def test_a_failing_event_is_retried_then_dead_lettered(member, paypal_signer, monkeypatch):
    source = paypal_source(member)
    paypal_signer.deliver(client, source, event("EVT-POISON"), WEBHOOK_ID)
    [row] = inbox(member.tenant, "EVT-POISON")

    real = processor.evidence.read_verified

    def fail_ours(conn, evidence_id, *args, **kwargs):
        if processor_row_is(conn, evidence_id, row.id):
            raise ValueError("evidence unreadable")
        return real(conn, evidence_id, *args, **kwargs)

    monkeypatch.setattr(processor.evidence, "read_verified", fail_ours)
    seen = []
    for _ in range(row.max_attempts + 1):
        process()
        process()  # not yet due: a failed row waits out its backoff
        [now] = inbox(member.tenant, "EVT-POISON")
        seen.append((now.status, now.attempts))
        make_due(member.tenant, now.id)

    assert seen == [
        *[("FAILED", n) for n in range(1, row.max_attempts)],
        ("DEAD_LETTER", row.max_attempts),
        ("DEAD_LETTER", row.max_attempts),  # never claimed again
    ]
    assert "evidence unreadable" in now.last_error
    assert records_for(member.tenant, "EVT-POISON") == 0


def make_due(tenant: UUID, inbox_id: UUID) -> None:
    with scoped_tx(tenant) as conn:
        conn.execute(
            text("UPDATE webhook_inbox SET next_attempt_at = now() WHERE id = :id"),
            {"id": inbox_id},
        )


def processor_row_is(conn, evidence_id: UUID, inbox_id: UUID) -> bool:
    return conn.execute(
        text("SELECT 1 FROM webhook_inbox WHERE id = :id AND raw_evidence_id = :e"),
        {"id": inbox_id, "e": evidence_id},
    ).first() is not None


# Out of order (spec 5.6, 10.4): needs a Finance tenant that reconciles PayPal events.


class WebhookTenant(PayPalTenant):
    """A PayPal tenant whose PayPal source has its webhook registered through the route."""

    def __init__(self) -> None:
        super().__init__()
        manifest = load_connector_manifests()["paypal"]
        with scoped_tx(self.tenant) as conn:
            conn.execute(
                text("UPDATE data_sources SET capabilities = CAST(:c AS jsonb) WHERE id = :id"),
                {"c": json.dumps([c.model_dump() for c in manifest.capabilities]),
                 "id": self.sources["paypal"]},
            )
        register_webhook(self, self.sources["paypal"], WEBHOOK_ID)

    def deliver(self, signer, payload: dict[str, Any]) -> None:
        response = signer.deliver(client, self.sources["paypal"], payload, WEBHOOK_ID)
        assert response.status_code == 200, response.text

    def capture(self, capture_id: str) -> dict[str, Any]:
        [entity] = [e for e in self.entities("finops:Capture") if capture_id in json.dumps(e)]
        return self.get(f"/api/entities/{entity['id']}")

    def runs(self) -> int:
        with scoped_tx(self.tenant) as conn:
            return conn.execute(text("SELECT count(*) FROM domain_runs")).scalar_one()


@pytest.fixture(scope="module")
def finance() -> WebhookTenant:
    return WebhookTenant()


def capture_event(capture_id: str, status: str, at: str) -> dict[str, Any]:
    resource = paypal_capture(capture_id, f"ORD-{capture_id}", status)
    return event(f"EVT-{capture_id}-{status}", f"PAYMENT.CAPTURE.{status}", resource, at)


def status_view(detail: dict[str, Any]) -> tuple[str, Any]:
    [view] = [v for v in detail["resolved"] if v["property_iri"] == "finops:lifecycleStatus"]
    return view["status"], view["value"]


def test_arrival_order_does_not_change_the_outcome(finance, evidence_root, paypal_signer):  # noqa: F811
    run = uuid4().hex[:8].upper()
    sequence = [("PENDING", "2026-03-01T00:00:00Z"), ("COMPLETED", "2026-03-01T00:05:00Z")]
    in_order, reversed_ = f"CAP-INORDER-{run}", f"CAP-REVERSED-{run}"
    for capture_id, arrivals in ((in_order, sequence), (reversed_, sequence[::-1])):
        for status, at in arrivals:
            finance.deliver(paypal_signer, capture_event(capture_id, status, at))
            process()  # one at a time, so processing order is arrival order

    a, b = finance.capture(in_order), finance.capture(reversed_)
    assert status_view(a) == status_view(b)
    for detail in (a, b):
        statuses = {o["normalized_value"]: o["occurred_at"]
                    for o in detail["observations"]
                    if o["property_iri"] == "finops:lifecycleStatus"}
        assert set(statuses) == {"PENDING", "COMPLETED"}  # neither event was dropped
        provider_times = {s: datetime.fromisoformat(at.replace("Z", "+00:00"))
                          for s, at in sequence}
        assert {s: datetime.fromisoformat(t) for s, t in statuses.items()} == provider_times


def test_an_event_before_its_merchant_row_still_reconciles(finance, evidence_root, paypal_signer):  # noqa: F811
    capture_id = f"CAP-EARLY-{uuid4().hex[:8].upper()}"
    finance.deliver(paypal_signer, capture_event(capture_id, "COMPLETED", "2026-03-02T00:00:00Z"))
    process()
    entity = finance.capture(capture_id)["id"]  # projected by the run the event triggered

    def mismatches() -> list[dict[str, Any]]:
        return [c for c in finance.cases(case_type="STATUS_MISMATCH") if entity in c["entity_ids"]]

    assert mismatches() == []

    # The merchant row arrives later; the next run finds the mismatch from the stored event.
    finance.merchant_payment(pending(capture_id, f"ORD-{capture_id}"))
    finance.run()
    [case] = mismatches()
    assert sorted(o["value"] for o in case["observed"]) == ["COMPLETED", "PENDING"]
    assert case["expected"] == "COMPLETED"


def test_redelivery_after_processing_starts_no_second_run(finance, evidence_root, paypal_signer):  # noqa: F811
    payload = capture_event(f"CAP-RERUN-{uuid4().hex[:8].upper()}", "COMPLETED",
                            datetime(2026, 3, 3, tzinfo=UTC).isoformat())
    finance.deliver(paypal_signer, payload)
    process()
    runs = finance.runs()

    finance.deliver(paypal_signer, payload)
    process()
    assert finance.runs() == runs
    assert records_for(finance.tenant, payload["id"]) == 1
