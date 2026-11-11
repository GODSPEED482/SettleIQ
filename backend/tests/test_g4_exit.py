"""Gate 4 exit: AC-WEBHOOK-01 and AC-PP-03/04/05/06/10/13 (delivery plan G4, spec 10.4, 21).

The exit asks for these with a real Sandbox `PAYMENT.CAPTURE.COMPLETED` event. Here the event
has the shape PayPal Sandbox delivers, is signed with PayPal's algorithm (transmission ID, time,
webhook ID and body CRC32 under SHA256withRSA), and goes through the public route, the
processor and the run it triggers, with nothing stubbed between them. What only a live Sandbox
delivery adds, PayPal's own certificate and transport, is the runbook step in README.md
("G4 live exit step").

TRACE maps each criterion to the tests holding its evidence, and
`test_every_traced_test_exists` keeps the map honest when tests are renamed.
"""

import importlib
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.db import scoped_tx
from tests.paypal_signing import foreign_signer, register_webhook
from tests.support import Actor, add_member, clear_overrides, grant_set
from tests.test_ingestion import evidence_root  # noqa: F401
from tests.test_paypal_mappings import pending
from tests.test_semantics_store import Member
from tests.test_webhook_failures import (
    WEBHOOK_ID,
    WebhookTenant,
    client,
    inbox,
    process,
    records_for,
    tenant_rows,
)

TRACE = {
    "AC-WEBHOOK-01": [
        "test_g4_exit::test_tenant_comes_from_the_registered_source_only",
        "test_paypal_g4::test_ac_webhook_01_trusted_source_ignores_payload_and_header_tenant",
        "test_webhook_failures::test_a_signature_for_another_tenants_webhook_is_refused",
    ],
    "AC-PP-03": [
        "test_g4_exit::test_a_forged_completion_changes_nothing",
        "test_webhook_failures::test_invalid_signature_writes_nothing_tenant_owned",
        "test_webhook_failures::test_unknown_source_writes_nothing",
        "test_paypal_g4::test_ac_pp_03_invalid_signature_leaves_no_tenant_row",
    ],
    "AC-PP-04": [
        "test_g4_exit::test_redelivery_is_deduplicated_end_to_end",
        "test_webhook_failures::test_redelivery_before_processing_is_one_inbox_row",
        "test_webhook_failures::test_redelivery_after_processing_changes_nothing",
        "test_webhook_inbox::test_replaying_an_already_recorded_event_adds_no_record",
    ],
    "AC-PP-05": [
        "test_g4_exit::test_sandbox_capture_completed_reaches_reconciliation",
        "test_paypal_g4::test_ac_pp_05_capture_completed_is_processed_into_a_source_record",
    ],
    "AC-PP-06": [
        "test_g4_exit::test_sandbox_capture_completed_reaches_reconciliation",
        "test_paypal_mappings::test_completed_event_against_pending_merchant_row_raises_status_mismatch",
        "test_webhook_failures::test_an_event_before_its_merchant_row_still_reconciles",
    ],
    "AC-PP-10": [
        "test_g4_exit::test_only_the_integration_keys_configure_paypal",
        "test_source_credentials::test_inherited_source_keys_cannot_manage_credentials_or_webhooks",
        "test_source_credentials::test_webhook_registration_reaches_the_trusted_lookup",
        "test_sources::test_a_reference_at_registration_needs_the_credential_key",
        "test_sources::test_registration_cannot_carry_webhook_configuration",
    ],
    "AC-PP-13": [
        "test_source_credentials::test_a_reference_names_a_secret_in_its_own_tenant_only",
        "test_secrets::test_a_tenant_cannot_reach_another_tenants_secret",
        "test_secrets::test_the_same_name_is_a_different_secret_in_each_tenant",
        "test_secrets::test_production_refuses_the_shared_environment",
    ],
}


@pytest.mark.parametrize("criterion", sorted(TRACE))
def test_every_traced_test_exists(criterion):
    for entry in TRACE[criterion]:
        module, name = entry.split("::")
        assert callable(getattr(importlib.import_module(f"tests.{module}"), name, None)), entry


def test_every_exit_criterion_is_traced():
    expected = {"AC-WEBHOOK-01"} | {f"AC-PP-{n:02}" for n in (3, 4, 5, 6, 10, 13)}
    assert set(TRACE) == expected


def sandbox_capture_completed(capture_id: str, order_ref: str) -> dict:
    """PAYMENT.CAPTURE.COMPLETED as PayPal Sandbox delivers it (Webhooks v1, Payments v2)."""
    event_id = f"WH-{uuid4().hex[:17].upper()}-{uuid4().hex[:17].upper()}"
    api = "https://api.sandbox.paypal.com"
    return {
        "id": event_id,
        "event_version": "1.0",
        "create_time": "2026-11-10T14:00:03.000Z",
        "resource_type": "capture",
        "resource_version": "2.0",
        "event_type": "PAYMENT.CAPTURE.COMPLETED",
        "summary": "Payment completed for $ 100.0 USD",
        "resource": {
            "id": capture_id,
            "status": "COMPLETED",
            "amount": {"currency_code": "USD", "value": "100.00"},
            "final_capture": True,
            "invoice_id": order_ref,
            "custom_id": order_ref,
            "seller_protection": {
                "status": "ELIGIBLE",
                "dispute_categories": ["ITEM_NOT_RECEIVED", "UNAUTHORIZED_TRANSACTION"],
            },
            "seller_receivable_breakdown": {
                "gross_amount": {"currency_code": "USD", "value": "100.00"},
                "paypal_fee": {"currency_code": "USD", "value": "3.98"},
                "net_amount": {"currency_code": "USD", "value": "96.02"},
            },
            "supplementary_data": {"related_ids": {"order_id": "5O190127TN364715T"}},
            "create_time": "2026-11-10T14:00:01Z",
            "update_time": "2026-11-10T14:00:01Z",
            "links": [
                {"href": f"{api}/v2/payments/captures/{capture_id}", "rel": "self",
                 "method": "GET"},
                {"href": f"{api}/v2/payments/captures/{capture_id}/refund", "rel": "refund",
                 "method": "POST"},
            ],
        },
        "links": [
            {"href": f"{api}/v1/notifications/webhooks-events/{event_id}", "rel": "self",
             "method": "GET"},
            {"href": f"{api}/v1/notifications/webhooks-events/{event_id}/resend",
             "rel": "resend", "method": "POST"},
        ],
    }


@pytest.fixture(scope="module")
def finance() -> WebhookTenant:
    return WebhookTenant()


def worker_runs(tenant) -> set:
    with scoped_tx(tenant) as conn:
        return set(conn.execute(
            text("SELECT id FROM domain_runs WHERE requested_by_service IS NOT NULL")
        ).scalars())


def event_record(tenant, event_id: str):
    with scoped_tx(tenant) as conn:
        return conn.execute(
            text(
                "SELECT r.id, r.record_type, e.retrieval_context->>'provenance' AS provenance "
                "FROM source_records r JOIN raw_evidence e ON e.id = r.raw_evidence_id "
                "WHERE r.external_id = :e"
            ),
            {"e": event_id},
        ).one()


@pytest.mark.db
def test_sandbox_capture_completed_reaches_reconciliation(finance, evidence_root, paypal_signer):  # noqa: F811
    """AC-PP-05 and AC-PP-06: the event becomes provider evidence, triggers a run, and that run
    raises STATUS_MISMATCH against the merchant's PENDING payment."""
    capture_id = f"9{uuid4().hex[:16].upper()}"
    order_ref = f"ORD-EXIT-{capture_id}"
    finance.merchant_payment(pending(capture_id, order_ref))
    payload = sandbox_capture_completed(capture_id, order_ref)
    runs_before = worker_runs(finance.tenant)

    finance.deliver(paypal_signer, payload)
    [row] = inbox(finance.tenant, payload["id"])
    assert row.status == "RECEIVED"  # receipt only verifies and stores
    process()

    [row] = inbox(finance.tenant, payload["id"])
    assert row.status == "PROCESSED"
    record = event_record(finance.tenant, payload["id"])
    assert record.record_type == "paypal:event/PAYMENT.CAPTURE.COMPLETED"
    assert record.provenance == "provider_event"
    [run] = worker_runs(finance.tenant) - runs_before  # started by the event, as the Worker

    entity = finance.capture(capture_id)["id"]
    [case] = [c for c in finance.cases(case_type="STATUS_MISMATCH") if entity in c["entity_ids"]]
    assert case["expected"] == "COMPLETED"  # PayPal is the capture-completion authority
    assert sorted(o["value"] for o in case["observed"]) == ["COMPLETED", "PENDING"]
    assert str(record.id) in case["evidence"]["source_record_ids"]
    with scoped_tx(finance.tenant) as conn:
        opened_by = conn.execute(
            text("SELECT run_id FROM domain_cases WHERE id = :id"), {"id": case["id"]}
        ).scalar_one()
    assert opened_by == run


@pytest.mark.db
def test_tenant_comes_from_the_registered_source_only(finance, evidence_root, paypal_signer):  # noqa: F811
    """AC-WEBHOOK-01: payload and header tenant claims are ignored."""
    elsewhere = Member("Claimed tenant")
    payload = sandbox_capture_completed(f"9{uuid4().hex[:16].upper()}", "ORD-CLAIM")
    payload["tenant_id"] = str(elsewhere.tenant)
    response = paypal_signer.deliver(
        client, finance.sources["paypal"], payload, WEBHOOK_ID,
        extra_headers={"X-Tenant-Id": str(elsewhere.tenant)},
    )
    assert response.status_code == 200
    assert len(inbox(finance.tenant, payload["id"])) == 1
    assert tenant_rows(elsewhere.tenant) == (0, 0)


@pytest.mark.db
def test_a_forged_completion_changes_nothing(finance, evidence_root, paypal_signer):  # noqa: F811
    """AC-PP-03: a well-formed event signed by a key PayPal did not issue is refused before any
    tenant is derived; no evidence, inbox row, record or run follows."""
    payload = sandbox_capture_completed(f"9{uuid4().hex[:16].upper()}", "ORD-FORGED")
    runs_before = worker_runs(finance.tenant)
    response = foreign_signer().deliver(client, finance.sources["paypal"], payload, WEBHOOK_ID)
    assert response.status_code == 400
    process()
    assert inbox(finance.tenant, payload["id"]) == []
    assert records_for(finance.tenant, payload["id"]) == 0
    assert worker_runs(finance.tenant) == runs_before


@pytest.mark.db
def test_redelivery_is_deduplicated_end_to_end(finance, evidence_root, paypal_signer):  # noqa: F811
    """AC-PP-04: PayPal's redelivery of a processed event adds no row, record or run."""
    payload = sandbox_capture_completed(f"9{uuid4().hex[:16].upper()}", "ORD-REDELIVERED")
    finance.deliver(paypal_signer, payload)
    process()
    runs_before = worker_runs(finance.tenant)

    finance.deliver(paypal_signer, payload)
    process()
    assert len(inbox(finance.tenant, payload["id"])) == 1
    assert records_for(finance.tenant, payload["id"]) == 1
    assert worker_runs(finance.tenant) == runs_before


@pytest.mark.db
def test_only_the_integration_keys_configure_paypal(finance):
    """AC-PP-10: Business Admin holds source.create and source.update, and still reaches
    neither the credential reference nor the webhook configuration, by any route."""
    source = finance.sources["paypal"]
    business, integration = Actor(), Actor()
    for actor, permission_set in ((business, "business_admin"),
                                  (integration, "integration_admin")):
        add_member(finance.tenant, actor.id)
        grant_set(finance.tenant, actor.id, permission_set)
    webhook = {"webhook_id": "WH-EXIT-10", "event_types": ["PAYMENT.CAPTURE.COMPLETED"]}
    reference = {"secret_reference": "env:SOURCE_SECRET_EXIT"}
    try:
        for path, body in ((f"/api/sources/{source}/webhooks", webhook),
                           (f"/api/sources/{source}/credentials", reference)):
            refused = business.request("PUT", path, tenant=finance.tenant, json=body)
            assert refused.status_code == 403
        smuggled = [
            {"connector_id": "paypal", "display_name": "Smuggled", **reference},
            {"connector_id": "paypal", "display_name": "Smuggled", "config": {"webhook": webhook}},
        ]
        for body in smuggled:
            created = business.request("POST", "/api/sources", tenant=finance.tenant, json=body)
            assert created.status_code in (403, 422), created.text

        put = integration.request(
            "PUT", f"/api/sources/{source}/webhooks", tenant=finance.tenant, json=webhook
        )
        assert put.status_code == 200, put.text
    finally:
        clear_overrides()
    with scoped_tx(finance.tenant) as conn:
        config = conn.execute(
            text("SELECT config FROM data_sources WHERE id = :id"), {"id": source}
        ).scalar_one()
    assert config["webhook"]["webhook_id"] == "WH-EXIT-10"
    register_webhook(finance, source, WEBHOOK_ID)  # the other tests sign for this one
