"""A Finance tenant ready for domain runs: four sources, a published ontology with real
mappings and authority, and a helper that ingests records the way connectors will (tasks 3.1-3.4).

Records go through `store_bytes` and `persist_record`, so every observation traces back to
hash-checked raw evidence. Until 3.5 and 3.6 land, this is the only ingestion path.
"""

import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text

from app.db import scoped_tx
from app.sources import conventions, evidence, records
from app.sources.envelope import SourceRecordEnvelope
from app.sources.evidence import RetrievalContext
from tests.support import grant_set
from tests.test_semantics_store import Member

EVIDENCE_ROOT = Path(tempfile.mkdtemp(prefix="settleiq-domain-"))

IDENTIFIER = {"datatype": "string", "value_basis": "identifier", "currency_field": None,
              "temporal_context": "any", "transform": {"name": "identity"}}
STATUS = {"datatype": "string", "value_basis": "status", "currency_field": None,
          "temporal_context": "current"}
CODE = {"datatype": "string", "value_basis": "code", "currency_field": None,
        "temporal_context": "any", "transform": {"name": "upper"}}


def money(currency_field: str) -> dict[str, Any]:
    return {"datatype": "decimal", "value_basis": "signed", "currency_field": currency_field,
            "temporal_context": "at_event", "transform": {"name": "decimal_string"}}


# (source, object, class, [(field_path, property, kind)])
BINDINGS = [
    ("paypal", "captures", "finops:Capture", [
        ("$.id", "finops:providerReference", IDENTIFIER),
        ("$.invoice_id", "finops:orderReference", IDENTIFIER),
        ("$.status", "finops:lifecycleStatus", STATUS | {"transform": {"name": "upper"}}),
        ("$.amount.value", "finops:amount", money("$.amount.currency_code")),
    ]),
    ("merchant", "payments", "finops:Capture", [
        ("$.paypal_capture_id", "finops:providerReference", IDENTIFIER),
        ("$.order_id", "finops:orderReference", IDENTIFIER),
        ("$.state", "finops:lifecycleStatus", STATUS | {"transform": {
            "name": "enum_map", "values": {"paid": "COMPLETED", "pending": "PENDING"}}}),
        ("$.amount", "finops:amount", money("$.currency")),
    ]),
    ("merchant", "orders", "finops:Order", [
        ("$.order_id", "finops:orderReference", IDENTIFIER),
        ("$.paypal_order_id", "finops:providerReference", IDENTIFIER),
    ]),
    ("report", "brr", "finops:SettlementEntry", [
        ("$.transaction_id", "finops:providerReference", IDENTIFIER),
        ("$.settlement_id", "finops:settlementReference", IDENTIFIER),
        ("$.event_code", "finops:entryType", CODE),
        ("$.gross", "finops:amount", money("$.currency")),
        ("$.fee", "finops:feeAmount", money("$.currency")),
        ("$.net", "finops:netAmount", money("$.currency")),
    ]),
    ("bank", "statement", "finops:BankTransaction", [
        ("$.bank_reference", "finops:providerReference", IDENTIFIER),
        ("$.memo", "finops:settlementReference", IDENTIFIER),
        ("$.amount", "finops:amount", money("$.currency")),
    ]),
]

# (property, entity type, authoritative source)
AUTHORITY = [
    ("finops:lifecycleStatus", "finops:Capture", "paypal"),
    ("finops:amount", "finops:Capture", "paypal"),
    ("finops:entryType", "finops:SettlementEntry", "report"),
    ("finops:amount", "finops:SettlementEntry", "report"),
    ("finops:feeAmount", "finops:SettlementEntry", "report"),
    ("finops:netAmount", "finops:SettlementEntry", "report"),
    ("finops:amount", "finops:BankTransaction", "bank"),
]

OBJECT_TYPES = {"captures": "API", "payments": "TABLE", "orders": "TABLE", "brr": "CSV",
                "statement": "CSV", "notes": "TABLE"}


class FinanceTenant(Member):
    """A member whose tenant has finops active and a published Finance ontology."""

    def __init__(self, authority: list[tuple] | None = None,
                 extra_bindings: list | None = None) -> None:
        super().__init__()
        grant_set(self.tenant, self.principal.id, "domain_operator")
        assert self.activate("finops").status_code == 200
        self.sources = {name: uuid4() for name in ("paypal", "merchant", "report", "bank")}
        self._register_sources()
        draft = self.create_draft().json()
        for source, obj, class_iri, fields in BINDINGS + (extra_bindings or []):
            for field_path, property_iri, kind in fields:
                self._ok(self.request("POST", "/api/mappings", json={
                    "draft_id": draft["id"], "source_id": str(self.sources[source]),
                    "external_object": obj, "field_path": field_path,
                    "property_iri": property_iri, "class_iri": class_iri, "nullable": True,
                    "is_unique": False, "sync_strategy": "batch", "unit": None,
                    "method": "human", **kind,
                }))
        for property_iri, entity_type, source, *valid_to in (
            AUTHORITY if authority is None else authority
        ):
            self._ok(self.request("POST", "/api/authority", json={
                "draft_id": draft["id"], "property_iri": property_iri,
                "entity_type_iri": entity_type, "source_id": str(self.sources[source]),
                "valid_from": "2026-01-01T00:00:00Z", "valid_to": (valid_to or [None])[0],
                "verification_method": "test",
            }))
        run = self.request("POST", "/api/ontology/validate", json={"draft_id": draft["id"]})
        published = self.request("POST", "/api/ontology/publish", json={
            "draft_id": draft["id"], "validation_run_id": run.json()["validation_run_id"],
            "confirm_publish": True,
        })
        self._ok(published)
        self.files: dict[str, str] = {}

    @staticmethod
    def _ok(response) -> None:
        assert response.status_code in (200, 201), response.text

    def _register_sources(self) -> None:
        with scoped_tx(self.tenant) as conn:
            for name, source_id in self.sources.items():
                conn.execute(
                    text(
                        """
                        INSERT INTO data_sources (id, tenant_id, connector_id,
                            connector_version, source_type, protocol, display_name, created_by)
                        VALUES (:id, :tenant, :connector, '1.0.0', 'test', 'http', :name, :user)
                        """
                    ),
                    {"id": source_id, "tenant": self.tenant, "connector": f"test-{name}",
                     "name": name, "user": self.principal.id},
                )

    def ingest(self, source: str, obj: str, attributes: dict[str, Any],
               external_id: str | None = None) -> UUID:
        """One record with its own raw evidence. File rows land in a `{name}@{sha16}` object."""
        data = json.dumps(attributes, sort_keys=True).encode()
        source_id = self.sources[source]
        with scoped_tx(self.tenant) as conn:
            ref = evidence.store_bytes(
                conn, tenant_id=self.tenant, source_id=source_id, data=data,
                evidence_type="RECORD", content_type="application/json",
                retrieval_context=RetrievalContext(provenance="synthetic_reference"),
                root=EVIDENCE_ROOT,
            )
            name = obj
            if OBJECT_TYPES[obj] == "CSV":
                first = conventions.file_object_name(obj, ref.content_hash)
                name = self.files.setdefault(obj, first)
            object_id = conventions.ensure_object(
                conn, tenant_id=self.tenant, source_id=source_id, object_name=name,
                object_type=OBJECT_TYPES[obj],
            )
            return records.persist_record(conn, SourceRecordEnvelope(
                id=uuid4(), tenant_id=self.tenant, source_id=source_id,
                source_object_id=object_id, external_id=external_id or str(uuid4()),
                record_type=f"test:{obj}", attributes=attributes, raw_evidence_id=ref.id,
                occurred_at=datetime(2026, 3, 1, tzinfo=UTC),
                observed_at=datetime(2026, 3, 1, 0, 0, 1, tzinfo=UTC),
                payload_hash=ref.content_hash, connector_version="1.0.0", mapping_version=None,
            ))

    def run(self, **body):
        response = self.request("POST", "/api/domain-runs", json=body or None)
        assert response.status_code == 201, response.text
        return response.json()

    def get(self, path: str, **params):
        response = self.request("GET", path, params=params)
        assert response.status_code == 200, response.text
        return response.json()

    def entities(self, class_iri: str) -> list[dict]:
        return self.get("/api/entities", class_iri=class_iri)

    def cases(self, **params) -> list[dict]:
        return self.get("/api/domain-cases", **params)


def capture(capture_id: str, order_id: str, amount: str, status: str = "COMPLETED",
            currency: str = "USD") -> dict[str, Any]:
    return {"id": capture_id, "invoice_id": order_id, "status": status,
            "amount": {"value": amount, "currency_code": currency}}


def brr(settlement: str, transaction: str, code: str, gross: str, fee: str | None = None,
        net: str | None = None, currency: str = "USD") -> dict[str, Any]:
    row = {"settlement_id": settlement, "transaction_id": transaction, "event_code": code,
           "gross": gross, "currency": currency}
    if fee is not None:
        row["fee"] = fee
    if net is not None:
        row["net"] = net
    return row


def seed_reconciliation(member: FinanceTenant) -> None:
    """The G3 fixture. It should raise exactly three cases:

    - STATUS_MISMATCH (INTRA_ENTITY): PayPal says CAP-1 is COMPLETED, the merchant says pending.
    - DUPLICATE_CAPTURE: CAP-2 and CAP-3 both succeeded for order ORD-2. CAP-4 and CAP-5 have
      equal amounts but different orders, so they raise nothing (AC-PLY-02).
    - SETTLEMENT_VARIANCE (INTER_ENTITY) on S-1: expected net 125.63 against a bank credit of
      124.63. Subtracting the netted fee again would give 122.43 (AC-REC-07). S-2 balances.

    The ORD-5/PO-6 order row matches two orders and must stay in review (AC-REC-06).
    """
    for order in ("ORD-1", "ORD-2", "ORD-3", "ORD-4"):
        member.ingest("merchant", "orders", {"order_id": order})
    member.ingest("merchant", "orders", {"order_id": "ORD-5", "paypal_order_id": "PO-5"})
    member.ingest("merchant", "orders", {"order_id": "ORD-6", "paypal_order_id": "PO-6"})

    member.ingest("paypal", "captures", capture("CAP-1", "ORD-1", "100.00"))
    member.ingest("merchant", "payments", {"paypal_capture_id": "CAP-1", "order_id": "ORD-1",
                                           "state": "pending", "amount": "100.00",
                                           "currency": "USD"})
    member.ingest("paypal", "captures", capture("CAP-2", "ORD-2", "50.00"))
    member.ingest("paypal", "captures", capture("CAP-3", "ORD-2", "50.00"))
    member.ingest("paypal", "captures", capture("CAP-4", "ORD-3", "75.00"))
    member.ingest("paypal", "captures", capture("CAP-5", "ORD-4", "75.00"))

    member.ingest("report", "brr", brr("S-1", "CAP-1", "T0006", "100.00", "-3.20", "96.80"))
    member.ingest("report", "brr", brr("S-1", "CAP-2", "T0006", "50.00", "-1.75"))
    member.ingest("report", "brr", brr("S-1", "REF-1", "T1107", "-20.00", "0.58", "-19.42"))
    member.ingest("report", "brr", brr("S-1", "WD-1", "T0400", "-107.63"))
    member.ingest("bank", "statement", {"bank_reference": "BNK-1", "memo": "S-1",
                                        "amount": "124.63", "currency": "USD"})
    member.ingest("report", "brr", brr("S-2", "CAP-4", "T0006", "75.00", "-2.00", "73.00"))
    member.ingest("bank", "statement", {"bank_reference": "BNK-2", "memo": "S-2",
                                        "amount": "73.00", "currency": "USD"})

    member.run()  # settles the first orders, so the mixed row below has two candidates
    member.ingest("merchant", "orders", {"order_id": "ORD-5", "paypal_order_id": "PO-6"})
