"""Domain projection and exact-ID identity resolution (task 3.1, spec 7.2, 7.6).

AC-REC-03 exact identifiers resolve; AC-REC-06 ambiguity stays reviewable; AC-REC-11 links
only through recorded decisions.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.db import scoped_tx
from app.domain.inputs import Record, RunInputs, object_keys
from tests.domain_fixture import IDENTIFIER, FinanceTenant
from tests.support import grant_set
from tests.test_semantics_store import Member

pytestmark = pytest.mark.db

CAPTURE = {"id": "CAP-1", "invoice_id": "ORD-1", "status": "COMPLETED",
           "amount": {"value": "100.00", "currency_code": "USD"}}
PAYMENT = {"paypal_capture_id": "CAP-1", "order_id": "ORD-1", "state": "paid",
           "amount": "100.00", "currency": "USD"}


@pytest.fixture(scope="module")
def tenant() -> FinanceTenant:
    return FinanceTenant()


def decisions(member: FinanceTenant, **where) -> list:
    clause = " AND ".join(f"{column} = :{column}" for column in where) or "true"
    with scoped_tx(member.tenant) as conn:
        return conn.execute(
            text(f"SELECT * FROM entity_resolution_decisions WHERE {clause} ORDER BY created_at"),
            where,
        ).all()


def links(member: FinanceTenant, record_id) -> list:
    with scoped_tx(member.tenant) as conn:
        return conn.execute(
            text("SELECT * FROM entity_source_links WHERE source_record_id = :r"),
            {"r": record_id},
        ).all()


def test_exact_identifiers_from_two_sources_resolve_to_one_entity():
    member = FinanceTenant()
    provider = member.ingest("paypal", "captures", CAPTURE)
    merchant = member.ingest("merchant", "payments", PAYMENT)
    member.run()
    captures = member.entities("finops:Capture")
    assert [c["canonical_key"] for c in captures] == ["finops:providerReference=CAP-1"]
    entity = member.get(f"/api/entities/{captures[0]['id']}")
    assert {link["source_record_id"] for link in entity["source_links"]} == {
        str(provider), str(merchant)
    }
    statuses = sorted(d.status for d in decisions(member, class_iri="finops:Capture"))
    assert statuses == ["AUTO_RESOLVED", "NEW_ENTITY"]


def test_ambiguous_identifiers_stay_pending_and_unlinked():
    member = FinanceTenant()
    member.ingest("merchant", "orders", {"order_id": "ORD-1", "paypal_order_id": "PO-1"})
    member.ingest("merchant", "orders", {"order_id": "ORD-2", "paypal_order_id": "PO-2"})
    member.run()
    # One merchant row names ORD-1 but PayPal order PO-2: two different orders.
    mixed = member.ingest("merchant", "orders", {"order_id": "ORD-1", "paypal_order_id": "PO-2"})
    member.run()
    member.run()  # a later run must not force it either
    (decision,) = decisions(member, source_record_id=mixed)
    assert decision.status == "PENDING_REVIEW"
    assert decision.selected_entity_id is None
    assert len(decision.candidate_entity_ids) == 2
    assert links(member, mixed) == []
    queue = member.get("/api/entity-resolution")
    assert [item["id"] for item in queue] == [str(decision.id)]


def test_every_link_comes_from_a_recorded_decision(tenant):
    tenant.ingest("paypal", "captures", CAPTURE | {"id": "CAP-L"})
    tenant.ingest("merchant", "payments", PAYMENT | {"paypal_capture_id": "CAP-L"})
    tenant.run()
    with scoped_tx(tenant.tenant) as conn:
        orphans = conn.execute(
            text(
                """
                SELECT count(*) FROM entity_source_links l
                LEFT JOIN entity_resolution_decisions d ON d.id = l.decision_id
                    AND d.source_record_id = l.source_record_id
                    AND d.selected_entity_id = l.entity_id
                WHERE d.id IS NULL
                """
            )
        ).scalar()
        linked = conn.execute(text("SELECT count(*) FROM entity_source_links")).scalar()
    assert linked > 0 and orphans == 0


def test_a_record_no_mapping_covers_is_preserved_and_unlinked(tenant):
    record = tenant.ingest("merchant", "notes", {"note": "call the customer"})
    tenant.run()
    assert decisions(tenant, source_record_id=record) == []
    assert links(tenant, record) == []
    with scoped_tx(tenant.tenant) as conn:
        kept = conn.execute(
            text("SELECT attributes FROM source_records WHERE id = :id"), {"id": record}
        ).scalar_one()
    assert kept == {"note": "call the customer"}


def test_a_record_without_identifiers_is_unmatched_not_guessed(tenant):
    record = tenant.ingest("paypal", "captures", {"status": "COMPLETED"})
    tenant.run()
    (decision,) = decisions(tenant, source_record_id=record)
    assert decision.status == "UNMATCHED"
    assert links(tenant, record) == []


def test_relationships_follow_shared_order_references():
    member = FinanceTenant()
    member.ingest("paypal", "captures", CAPTURE)
    member.ingest("merchant", "orders", {"order_id": "ORD-1"})
    member.run()
    (capture,) = member.entities("finops:Capture")
    (order,) = member.entities("finops:Order")
    with scoped_tx(member.tenant) as conn:
        rows = conn.execute(
            text("SELECT subject_entity_id, predicate_iri, object_entity_id "
                 "FROM domain_relationship_instances")
        ).all()
    assert [(str(r[0]), r[1], str(r[2])) for r in rows] == [
        (capture["id"], "finops:forOrder", order["id"])
    ]


# Review


@pytest.fixture
def pending() -> tuple[FinanceTenant, object]:
    member = FinanceTenant()
    member.ingest("merchant", "orders", {"order_id": "ORD-1", "paypal_order_id": "PO-1"})
    member.ingest("merchant", "orders", {"order_id": "ORD-2", "paypal_order_id": "PO-2"})
    member.run()
    record = member.ingest("merchant", "orders", {"order_id": "ORD-1", "paypal_order_id": "PO-2"})
    member.run()
    (decision,) = decisions(member, source_record_id=record)
    return member, decision


def review(member, decision, **body):
    return member.request(
        "POST", f"/api/entity-resolution/{decision.id}/decision", json=body
    )


def test_accepting_links_the_record_and_is_audited(pending):
    member, decision = pending
    chosen = decision.candidate_entity_ids[0]
    response = review(member, decision, outcome="ACCEPT", candidate_entity_id=str(chosen),
                      reason="Same order; PO-2 was mistyped")
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["status"], body["selected_entity_id"]) == ("ACCEPTED", str(chosen))
    assert body["reviewer_id"] == str(member.principal.id)
    assert [link.entity_id for link in links(member, decision.source_record_id)] == [chosen]
    with scoped_tx(member.tenant) as conn:
        audit = conn.execute(
            text("SELECT detail FROM audit_logs WHERE action = 'entity_resolution.reviewed'")
        ).scalar_one()
    assert audit["outcome"] == "ACCEPTED"
    member.run()  # the next run observes the newly linked record
    entity = member.get(f"/api/entities/{chosen}")
    assert str(decision.source_record_id) in {o["source_record_id"] for o in entity["observations"]}


def test_rejecting_leaves_the_record_unlinked(pending):
    member, decision = pending
    response = review(member, decision, outcome="REJECT", reason="Different orders")
    assert response.status_code == 200
    assert response.json()["status"] == "REJECTED"
    assert links(member, decision.source_record_id) == []


def test_a_settled_decision_cannot_be_reviewed_again(pending):
    member, decision = pending
    review(member, decision, outcome="REJECT", reason="no")
    again = review(member, decision, outcome="REJECT", reason="no")
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "DECISION_NOT_PENDING"


def test_only_a_listed_candidate_can_be_accepted(pending):
    member, decision = pending
    response = review(member, decision, outcome="ACCEPT", candidate_entity_id=str(uuid4()),
                      reason="x")
    assert response.status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"outcome": "ACCEPT", "reason": "x"},
        {"outcome": "REJECT", "candidate_entity_id": "00000000-0000-4000-8000-000000000000",
         "reason": "x"},
        {"outcome": "REJECT"},
        {"outcome": "REJECT", "reason": "x", "reviewer_id": "00000000-0000-4000-8000-000000000000"},
    ],
)
def test_review_body_is_strict(pending, body):
    member, decision = pending
    assert review(member, decision, **body).status_code == 422


def test_another_tenants_entities_and_decisions_are_404(pending):
    member, decision = pending
    other = Member()
    grant_set(other.tenant, other.principal.id, "domain_operator")
    (order, *_) = member.entities("finops:Order")
    assert other.request("GET", f"/api/entities/{order['id']}").status_code == 404
    response = other.request(
        "POST", f"/api/entity-resolution/{decision.id}/decision",
        json={"outcome": "REJECT", "reason": "x"},
    )
    assert response.status_code == 404
    assert decisions(member, id=decision.id)[0].status == "PENDING_REVIEW"


# Which mappings a file row answers to


def record(object_name: str, record_type: str) -> Record:
    return Record(uuid4(), SOURCE, object_name, record_type, {}, uuid4(), "0" * 64, None,
                  datetime(2026, 3, 1, tzinfo=UTC))


def inputs(*external_objects: str) -> RunInputs:
    mappings = [{"id": name, "source_id": str(SOURCE), "external_object": name}
                for name in external_objects]
    return RunInputs(None, uuid4(), 1, "0" * 64, mappings, [], None, [], datetime.now(UTC))


SOURCE = uuid4()
MARCH = record("march.csv@0123456789abcdef", "paypal:file/BRR")


def test_a_file_row_answers_to_its_object_its_file_and_its_report_kind():
    assert object_keys(MARCH) == ["march.csv@0123456789abcdef", "march.csv", "BRR"]


def test_a_table_row_answers_only_to_its_table():
    assert object_keys(record("payments", "merchant:table/public.payments")) == ["payments"]


COMPLETED = record("events", "paypal:event/PAYMENT.CAPTURE.COMPLETED")


def test_an_event_answers_to_its_event_type_before_its_stream():
    assert object_keys(COMPLETED) == ["PAYMENT.CAPTURE.COMPLETED", "events"]


@pytest.mark.parametrize(
    ("available", "chosen"),
    [
        (["events", "PAYMENT.CAPTURE.COMPLETED"], "PAYMENT.CAPTURE.COMPLETED"),
        (["events", "PAYMENT.CAPTURE.REFUNDED"], "events"),
        (["PAYMENT.CAPTURE.REFUNDED"], None),
    ],
)
def test_an_event_type_mapping_wins_over_the_stream(available, chosen):
    found = [m["external_object"] for m in inputs(*available).mappings_for(COMPLETED)]
    assert found == ([chosen] if chosen else [])


@pytest.mark.parametrize(
    ("available", "chosen"),
    [
        (["BRR"], "BRR"),
        (["BRR", "march.csv"], "march.csv"),
        (["BRR", "march.csv", "march.csv@0123456789abcdef"], "march.csv@0123456789abcdef"),
        (["BANK_CSV", "april.csv"], None),
    ],
)
def test_the_most_specific_mapping_wins(available, chosen):
    found = [m["external_object"] for m in inputs(*available).mappings_for(MARCH)]
    assert found == ([chosen] if chosen else [])


def test_another_sources_mapping_never_applies():
    other = inputs("BRR")
    other.mappings[0]["source_id"] = str(uuid4())
    assert other.mappings_for(MARCH) == []


def test_a_report_kind_mapping_covers_every_upload_of_that_kind():
    kind_binding = ("report", "BRR", "finops:SettlementEntry", [
        ("$['Transaction ID']", "finops:providerReference", IDENTIFIER),
    ])
    member = FinanceTenant(extra_bindings=[kind_binding])
    march = member.upload_row("report", "march.csv", "BRR", {"Transaction ID": "TX-1"})
    april = member.upload_row("report", "april.csv", "BRR", {"Transaction ID": "TX-2"})
    bank = member.upload_row("report", "april.csv", "BANK_CSV", {"Transaction ID": "TX-3"})
    member.run()
    keys = [e["canonical_key"] for e in member.entities("finops:SettlementEntry")]
    assert keys == ["finops:providerReference=TX-1", "finops:providerReference=TX-2"]
    assert len(links(member, march)) == len(links(member, april)) == 1
    assert decisions(member, source_record_id=bank) == []  # no BANK_CSV mapping: preserved
