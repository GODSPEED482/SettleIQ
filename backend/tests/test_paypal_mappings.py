"""PayPal field mappings and capture-completion authority (task 4.6, spec 10.5, AC-PP-06).

The finops pack ships PayPal bindings and authority as hints. Applied to a draft for a real
PayPal source and published, a verified `PAYMENT.CAPTURE.COMPLETED` event and a merchant row
still `PENDING` become comparable observations of one capture, and the run raises
STATUS_MISMATCH with PayPal's value as the authoritative one.

Webhook ingestion is task 4.3, so the event is stored here exactly as conventions.py says the
webhook route will store it.
"""

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from app.db import scoped_tx
from app.domain_packs.manifest import load_domain_packs
from app.semantics.mappings import hint_bodies
from app.sources import conventions, evidence, records
from app.sources.envelope import SourceRecordEnvelope
from app.sources.evidence import RetrievalContext
from tests.domain_fixture import EVIDENCE_ROOT, IDENTIFIER, STATUS, FinanceTenant, money
from tests.support import grant_set
from tests.test_semantics_store import Member

pytestmark = pytest.mark.db

VALID_FROM = datetime(2026, 1, 1, tzinfo=UTC)

# The merchant's own schema is tenant-specific, so it is bound by hand, as a person would.
MERCHANT_PAYMENTS = [
    ("$.paypal_capture_id", "finops:providerReference", IDENTIFIER),
    ("$.order_id", "finops:orderReference", IDENTIFIER),
    ("$.state", "finops:lifecycleStatus", STATUS | {"transform": {
        "name": "enum_map", "values": {"paid": "COMPLETED", "pending": "PENDING"}}}),
    ("$.amount", "finops:amount", money("$.currency")),
]


class PayPalTenant(FinanceTenant):
    """A Finance tenant whose PayPal bindings come only from the pack's hints."""

    def __init__(self) -> None:
        Member.__init__(self)
        grant_set(self.tenant, self.principal.id, "domain_operator")
        assert self.activate("finops").status_code == 200
        self.sources = {"paypal": uuid4(), "merchant": uuid4()}
        self._register(self.sources["paypal"], "paypal", "payment_provider", "https-rest")
        self._register(self.sources["merchant"], "merchant-postgres", "merchant_database",
                       "postgresql")
        draft = self.create_draft().json()
        self.draft_id = UUID(draft["id"])
        pack = load_domain_packs()["finops"]
        for connector, source in (("paypal", "paypal"), ("merchant-postgres", "merchant")):
            bindings, authority = hint_bodies(
                pack, connector, self.draft_id, self.sources[source], VALID_FROM
            )
            for body in bindings:
                self._ok(self.request("POST", "/api/mappings", json=body.model_dump(mode="json")))
            for body in authority:
                self._ok(self.request("POST", "/api/authority", json=body.model_dump(mode="json")))
        for field_path, property_iri, kind in MERCHANT_PAYMENTS:
            self._ok(self.request("POST", "/api/mappings", json={
                "draft_id": draft["id"], "source_id": str(self.sources["merchant"]),
                "external_object": "payments", "field_path": field_path,
                "property_iri": property_iri, "class_iri": "finops:Capture", "nullable": True,
                "is_unique": False, "sync_strategy": "batch", "unit": None, "method": "human",
                **kind,
            }))
        run = self.request("POST", "/api/ontology/validate", json={"draft_id": draft["id"]})
        assert run.status_code == 200, run.text
        assert run.json()["passed"] is True, run.text
        self._ok(self.request("POST", "/api/ontology/publish", json={
            "draft_id": draft["id"], "validation_run_id": run.json()["validation_run_id"],
            "confirm_publish": True,
        }))

    def _register(self, source_id: UUID, connector: str, source_type: str, protocol: str) -> None:
        with scoped_tx(self.tenant) as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO data_sources (id, tenant_id, connector_id, connector_version,
                        source_type, protocol, display_name, created_by)
                    VALUES (:id, :tenant, :connector, '1.1.0', :type, :protocol, :name, :user)
                    """
                ),
                {"id": source_id, "tenant": self.tenant, "connector": connector,
                 "type": source_type, "protocol": protocol, "name": connector,
                 "user": self.principal.id},
            )

    def _persist(self, source: str, *, object_name: str, object_type: str, record_type: str,
                 external_id: str, attributes: dict[str, Any], evidence_type: str,
                 context: RetrievalContext, provider_event_id: str | None = None) -> UUID:
        source_id = self.sources[source]
        data = json.dumps(attributes, sort_keys=True).encode()
        with scoped_tx(self.tenant) as conn:
            ref = evidence.store_bytes(
                conn, tenant_id=self.tenant, source_id=source_id, data=data,
                evidence_type=evidence_type, content_type="application/json",
                retrieval_context=context, provider_event_id=provider_event_id,
                root=EVIDENCE_ROOT,
            )
            object_id = conventions.ensure_object(
                conn, tenant_id=self.tenant, source_id=source_id, object_name=object_name,
                object_type=object_type,
            )
            return records.persist_record(conn, SourceRecordEnvelope(
                id=uuid4(), tenant_id=self.tenant, source_id=source_id,
                source_object_id=object_id, external_id=external_id, record_type=record_type,
                attributes=attributes, raw_evidence_id=ref.id,
                occurred_at=datetime(2026, 3, 1, tzinfo=UTC),
                observed_at=datetime(2026, 3, 1, 0, 0, 1, tzinfo=UTC),
                payload_hash=ref.content_hash, connector_version="1.1.0", mapping_version=None,
            ))

    def event(self, event_type: str, resource: dict[str, Any]) -> UUID:
        """One verified webhook delivery, stored the way conventions.py defines for events."""
        event_id = f"WH-{uuid4()}"
        return self._persist(
            "paypal", object_name=conventions.EVENT_OBJECT, object_type="EVENT",
            record_type=conventions.event_record_type("paypal", event_type),
            external_id=event_id, evidence_type="WEBHOOK", provider_event_id=event_id,
            context=RetrievalContext(provenance="provider_event", event_type=event_type),
            attributes={"id": event_id, "event_type": event_type,
                        "create_time": "2026-03-01T00:00:00Z", "resource": resource},
        )

    def capture_read(self, resource: dict[str, Any]) -> UUID:
        return self._persist(
            "paypal", object_name="captures.read", object_type="API",
            record_type="paypal:capture", external_id=resource["id"], evidence_type="RECORD",
            context=RetrievalContext(provenance="provider_api"), attributes=resource,
        )

    def merchant_payment(self, row: dict[str, Any]) -> UUID:
        return self._persist(
            "merchant", object_name="payments", object_type="TABLE",
            record_type=conventions.database_record_type("merchant-postgres", "public",
                                                         "payments"),
            external_id=row["paypal_capture_id"], evidence_type="RECORD",
            context=RetrievalContext(provenance="database"), attributes=row,
        )


def paypal_capture(capture_id: str, order_id: str, status: str = "COMPLETED") -> dict[str, Any]:
    """A capture as PayPal returns it, in captures.read and as an event's resource."""
    return {"id": capture_id, "invoice_id": order_id, "status": status,
            "amount": {"currency_code": "USD", "value": "100.00"},
            "create_time": "2026-03-01T00:00:00Z", "update_time": "2026-03-01T00:00:05Z",
            "final_capture": True}


def pending(capture_id: str, order_id: str) -> dict[str, Any]:
    return {"paypal_capture_id": capture_id, "order_id": order_id, "state": "pending",
            "amount": "100.00", "currency": "USD"}


@pytest.fixture(scope="module")
def tenant() -> PayPalTenant:
    return PayPalTenant()


def test_hints_publish_paypal_as_the_capture_completion_authority(tenant):
    authority = tenant.get("/api/authority", draft_id=str(tenant.draft_id))
    by_term = {(a["property_iri"], a["entity_type_iri"]): a["source_id"] for a in authority}
    paypal, merchant = (str(tenant.sources[name]) for name in ("paypal", "merchant"))
    assert by_term[("finops:lifecycleStatus", "finops:Capture")] == paypal
    assert by_term[("finops:lifecycleStatus", "finops:Order")] == merchant
    assert by_term[("finops:lifecycleStatus", "finops:Payment")] == merchant


def test_completed_event_against_pending_merchant_row_raises_status_mismatch(tenant):
    tenant.event("PAYMENT.CAPTURE.COMPLETED", paypal_capture("CAP-EVT-1", "ORD-EVT-1"))
    tenant.merchant_payment(pending("CAP-EVT-1", "ORD-EVT-1"))
    run = tenant.run()

    [finding] = [f for f in run["findings"] if f["case_type"] == "STATUS_MISMATCH"
                 and f["new_case"]]
    [case] = [c for c in tenant.cases(case_type="STATUS_MISMATCH")
              if sorted(o["value"] for o in c["observed"]) == ["COMPLETED", "PENDING"]
              and c["fingerprint"] == finding["fingerprint"]]
    assert case["reconciliation_scope"] == "INTRA_ENTITY"
    assert case["property_iris"] == ["finops:lifecycleStatus"]
    assert case["expected"] == "COMPLETED"  # PayPal's value, as the authority
    sources = {o["source_id"] for o in case["observed"]}
    assert sources == {str(tenant.sources["paypal"]), str(tenant.sources["merchant"])}
    assert len(case["evidence"]["source_record_ids"]) == 2


def test_event_and_capture_read_are_comparable_observations_of_one_capture(tenant):
    tenant.event("PAYMENT.CAPTURE.COMPLETED", paypal_capture("CAP-EVT-2", "ORD-EVT-2"))
    tenant.capture_read(paypal_capture("CAP-EVT-2", "ORD-EVT-2"))
    tenant.run()
    [capture] = [e for e in tenant.entities("finops:Capture")
                 if "CAP-EVT-2" in json.dumps(e)]
    detail = tenant.get(f"/api/entities/{capture['id']}")
    statuses = [o for o in detail["observations"]
                if o["property_iri"] == "finops:lifecycleStatus"]
    assert len(statuses) == 2 and {o["normalized_value"] for o in statuses} == {"COMPLETED"}
    assert not [c for c in tenant.cases(case_type="STATUS_MISMATCH")
                if capture["id"] in c["entity_ids"]]


def test_refund_event_binds_a_refund_not_a_capture(tenant):
    refund = paypal_capture("REF-EVT-3", "ORD-EVT-3")
    tenant.event("PAYMENT.CAPTURE.REFUNDED", refund)
    tenant.run()
    assert [e for e in tenant.entities("finops:Refund") if "REF-EVT-3" in json.dumps(e)]
    assert not [e for e in tenant.entities("finops:Capture") if "REF-EVT-3" in json.dumps(e)]
