"""The Finance engine and the G3 exit criteria (tasks 3.3, 3.10, spec 7.3-7.5, 17).

Exit: identical version-pinned inputs give identical findings; fees already in net are never
subtracted again (AC-REC-07); an ambiguous identity match is never forced (AC-REC-06).
"""

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.db import scoped_tx
from app.domain.inputs import Record
from app.domain.views import current_word
from packs.finops import engine
from tests.domain_fixture import FinanceTenant, brr, capture, seed_reconciliation

# Database: the full pipeline over the G3 fixture


@pytest.fixture(scope="module")
def reconciled() -> tuple[FinanceTenant, dict, dict]:
    member = FinanceTenant()
    seed_reconciliation(member)
    run = member.run()
    cases = {case["case_type"]: case for case in member.cases()}
    return member, run, cases


@pytest.mark.db
def test_the_fixture_raises_exactly_its_three_cases(reconciled):
    _, run, cases = reconciled
    assert run["status"] == "SUCCEEDED"
    assert run["rule_version"] == "finops@0.2.0/finops.engine"
    assert sorted(cases) == ["DUPLICATE_CAPTURE", "SETTLEMENT_VARIANCE", "STATUS_MISMATCH"]
    assert sorted(f["case_type"] for f in run["findings"]) == sorted(cases)


@pytest.mark.db
def test_identical_pinned_inputs_give_identical_findings(reconciled):
    member, first, _ = reconciled
    second = member.run()
    assert second["inputs_hash"] == first["inputs_hash"]
    assert [f["fingerprint"] for f in second["findings"]] == [
        f["fingerprint"] for f in first["findings"]
    ]
    assert not any(f["new_case"] for f in second["findings"])  # no duplicate cases
    assert len(member.cases()) == 3


@pytest.mark.db
def test_fees_already_in_net_are_never_subtracted_again(reconciled):
    _, _, cases = reconciled
    variance = cases["SETTLEMENT_VARIANCE"]
    assert variance["reconciliation_scope"] == "INTER_ENTITY"
    # 96.80 (net already includes its 3.20 fee) + 48.25 (50.00 gross less its fee once)
    # - 19.42 (refund net); the T0400 withdrawal is the bank side, not part of the net.
    assert (variance["expected_amount"], variance["observed_amount"]) == (
        "125.630000", "124.630000"
    )
    assert variance["currency"] == "USD"
    assert variance["evidence"]["finding"]["variance"] == "1.000000"
    assert len(variance["evidence"]["finding"]["excluded_entries"]) == 1


@pytest.mark.db
def test_an_ambiguous_identity_match_is_never_forced(reconciled):
    member, _, _ = reconciled
    with scoped_tx(member.tenant) as conn:
        pending = conn.execute(
            text(
                """
                SELECT d.source_record_id, d.candidate_entity_ids, d.selected_entity_id,
                    (SELECT count(*) FROM entity_source_links l
                     WHERE l.source_record_id = d.source_record_id) AS links
                FROM entity_resolution_decisions d WHERE d.status = 'PENDING_REVIEW'
                """
            )
        ).all()
    assert len(pending) == 1
    assert (len(pending[0].candidate_entity_ids), pending[0].selected_entity_id,
            pending[0].links) == (2, None, 0)


@pytest.mark.db
def test_status_mismatch_is_an_intra_entity_finding_with_its_observations(reconciled):
    member, _, cases = reconciled
    mismatch = cases["STATUS_MISMATCH"]
    assert mismatch["reconciliation_scope"] == "INTRA_ENTITY"
    assert mismatch["property_iris"] == ["finops:lifecycleStatus"]
    assert mismatch["expected"] == "COMPLETED"
    assert sorted(o["value"] for o in mismatch["observed"]) == ["COMPLETED", "PENDING"]
    assert len(mismatch["observation_ids"]) == 2
    assert len(mismatch["evidence"]["source_record_ids"]) == 2
    (entity,) = mismatch["entity_ids"]
    assert mismatch["evidence"]["resolution"][entity]["finops:lifecycleStatus"] == "RESOLVED"


@pytest.mark.db
def test_duplicate_capture_needs_two_successful_captures_for_one_order(reconciled):
    member, _, cases = reconciled
    duplicate = cases["DUPLICATE_CAPTURE"]
    assert duplicate["severity"] == "critical"
    assert duplicate["observed"]["capture_references"] == ["CAP-2", "CAP-3"]
    assert len(duplicate["entity_ids"]) == 3 and len(duplicate["relationship_ids"]) == 2
    # CAP-4 and CAP-5 share an amount but not an order: nothing was raised for them.
    assert duplicate["observed"]["successful_captures"] == 2


@pytest.mark.db
def test_every_case_carries_what_ac_rec_08_requires(reconciled):
    _, run, cases = reconciled
    for case in cases.values():
        assert case["entity_ids"] and case["observation_ids"]
        assert case["evidence"]["source_record_ids"]
        assert case["evidence"]["resolution"]
        # The fixture's evidence is reference data, and the case says so (spec 10.3.1).
        assert case["evidence_provenance"] == ["synthetic_reference"]
        assert case["method"] and case["observed"] is not None
        assert case["version_refs"]["rule_version"] == run["rule_version"]
        assert case["version_refs"]["ontology_content_hash"] == run["ontology_content_hash"]


@pytest.mark.db
def test_a_corrected_row_is_read_as_its_source_last_reported_it():
    """A re-synced row supersedes the source's earlier report of it, so once the merchant says
    COMPLETED the PENDING it said before is history, not a mismatch (spec 5.5)."""
    member = FinanceTenant()
    member.ingest("merchant", "orders", {"order_id": "ORD-9"}, external_id="ORD-9")
    member.ingest("paypal", "captures", capture("CAP-9", "ORD-9", "29.00"), external_id="CAP-9")
    payment = {"paypal_capture_id": "CAP-9", "order_id": "ORD-9", "state": "pending",
               "amount": "29.00", "currency": "USD"}
    member.ingest("merchant", "payments", payment, external_id="PAY-9")
    assert [f["case_type"] for f in member.run()["findings"]] == ["STATUS_MISMATCH"]

    member.ingest("merchant", "payments", {**payment, "state": "completed"},
                  external_id="PAY-9")
    assert member.run()["findings"] == []
    with scoped_tx(member.tenant) as conn:
        kept = conn.execute(
            text("SELECT count(*) FROM source_records WHERE external_id = 'PAY-9'")
        ).scalar_one()
    assert kept == 2  # the earlier report stays as history


@pytest.mark.db
def test_a_pending_capture_is_not_a_duplicate():
    member = FinanceTenant()
    member.ingest("merchant", "orders", {"order_id": "ORD-9"})
    member.ingest("paypal", "captures", capture("CAP-8", "ORD-9", "10.00"))
    member.ingest("paypal", "captures", capture("CAP-9", "ORD-9", "10.00", status="PENDING"))
    member.run()
    assert member.cases(case_type="DUPLICATE_CAPTURE") == []


@pytest.mark.db
def test_settlement_in_another_currency_is_never_compared():
    member = FinanceTenant()
    member.ingest("report", "brr", brr("S-9", "CAP-1", "T0006", "100.00", currency="EUR"))
    member.ingest("bank", "statement", {"bank_reference": "B-9", "memo": "S-9",
                                        "amount": "90.00", "currency": "USD"})
    member.run()
    assert member.cases(case_type="SETTLEMENT_VARIANCE") == []


@pytest.mark.db
def test_a_dismissed_finding_is_not_raised_again_from_the_same_evidence():
    """Spec 9.3: a dismissed or closed case comes back only through reopen."""
    member = FinanceTenant()
    member.ingest("paypal", "captures", capture("CAP-7", "ORD-7", "29.00"))
    member.ingest("merchant", "payments", {"paypal_capture_id": "CAP-7", "order_id": "ORD-7",
                                           "state": "pending", "amount": "29.00",
                                           "currency": "USD"})
    [first] = member.run()["findings"]
    with scoped_tx(member.tenant) as conn:
        conn.execute(text("UPDATE domain_cases SET status = 'DISMISSED', reason = 'noise' "
                          "WHERE id = :id"), {"id": first["case_id"]})
    [again] = member.run()["findings"]
    assert (again["case_id"], again["new_case"]) == (first["case_id"], False)
    assert [c["status"] for c in member.cases()] == ["DISMISSED"]


@pytest.mark.db
def test_unresolved_amounts_are_not_guessed():
    member = FinanceTenant(authority=[])  # no authority at all
    member.ingest("report", "brr", brr("S-8", "CAP-1", "T0006", "100.00", "-3.00", "97.00"))
    member.ingest("bank", "statement", {"bank_reference": "B-8", "memo": "S-8",
                                        "amount": "1.00", "currency": "USD"})
    member.run()
    assert member.cases(case_type="SETTLEMENT_VARIANCE") == []


# No database: the engine over hand-built snapshots


def observation(oid: str, entity: str, source: str, value: str, *, prop="finops:amount",
                currency="USD", context="at_event") -> dict:
    numeric = value if prop == "finops:amount" else None
    return {"id": oid, "entity_id": entity, "property_iri": prop, "source_id": source,
            "value": value, "numeric": numeric, "currency": currency, "unit": None,
            "value_basis": "signed", "temporal_context": context, "occurred_at": None}


def snapshot(observations: list[dict]) -> dict:
    return {"as_of": "2026-03-01T00:00:00+00:00",
            "entities": [{"id": "e1", "class_iri": "finops:Capture"}],
            "observations": observations, "views": [], "relationships": []}


def test_amounts_from_two_sources_that_differ_are_an_amount_mismatch():
    found = engine.run(snapshot([observation("o1", "e1", "s1", "100.000000"),
                                 observation("o2", "e1", "s2", "90.000000")]))
    assert [(f["case_type"], f["scope"]) for f in found] == [("AMOUNT_MISMATCH", "INTRA_ENTITY")]


@pytest.mark.parametrize("change", [{"currency": "EUR"}, {"context": "at_settlement"}])
def test_different_currencies_or_time_contexts_are_not_compared(change):
    found = engine.run(snapshot([observation("o1", "e1", "s1", "100.000000"),
                                 observation("o2", "e1", "s2", "90.000000", **change)]))
    assert found == []


def test_one_source_disagreeing_with_itself_is_left_to_the_resolved_view():
    found = engine.run(snapshot([observation("o1", "e1", "s1", "100.000000"),
                                 observation("o2", "e1", "s1", "90.000000")]))
    assert found == []


def test_equal_values_raise_nothing():
    assert engine.run(snapshot([observation("o1", "e1", "s1", "100.000000"),
                                observation("o2", "e1", "s2", "100.000000")])) == []


def test_the_engine_is_deterministic():
    state = snapshot([observation("o2", "e1", "s2", "90.000000"),
                      observation("o1", "e1", "s1", "100.000000")])
    assert engine.run(state) == engine.run(state)
    assert Decimal(engine.run(state)[0]["observed"][0]["value"]) == Decimal("100")


# No database: only a source's current word on a value is eligible (spec 5.5)

T1, T2, T3 = (datetime(2026, 3, 1, hour, tzinfo=UTC) for hour in (1, 2, 3))


def said(value: str, *, at: datetime, event: bool = False, source: str = "paypal",
         occurred: datetime | None = None) -> tuple[SimpleNamespace, Record]:
    """One observation of a capture's status and the record it came from. An event speaks for
    its provider time (`at`) whenever it arrived; a read for when it was read."""
    record = Record(uuid4(), source, "events" if event else "captures.read",
                    "paypal:event/PAYMENT.CAPTURE.X" if event else "paypal:table/x", {},
                    uuid4(), "0" * 64, at if event else occurred, T3 if event else at)
    return SimpleNamespace(id=uuid4(), entity_id="e1", property_iri="finops:lifecycleStatus",
                           source_id=source, source_record_id=record.id,
                           normalized_value=value), record


def eligible(*said_: tuple[SimpleNamespace, Record]) -> list[str]:
    records = {record.id: record for _, record in said_}
    return sorted(o.normalized_value for o in current_word([o for o, _ in said_], records))


def test_a_later_read_supersedes_an_earlier_event():
    assert eligible(said("PENDING", at=T1, event=True), said("COMPLETED", at=T2)) == ["COMPLETED"]


def test_a_later_event_supersedes_an_earlier_read():
    assert eligible(said("PENDING", at=T1), said("COMPLETED", at=T2, event=True)) == ["COMPLETED"]


def test_events_follow_provider_time_not_arrival():
    # Both arrive at T3; the one PayPal raised later wins, whichever was processed first.
    assert eligible(said("COMPLETED", at=T2, event=True),
                    said("PENDING", at=T1, event=True)) == ["COMPLETED"]


def test_a_read_after_an_event_wins_even_with_an_older_create_time():
    # A captures.read carries the capture's create time; it still speaks for when it was read.
    assert eligible(said("COMPLETED", at=T2, event=True),
                    said("REFUNDED", at=T3, occurred=T1)) == ["REFUNDED"]


def test_two_rows_of_one_source_that_disagree_both_stay():
    assert eligible(said("PENDING", at=T1), said("COMPLETED", at=T2)) == ["COMPLETED", "PENDING"]


def test_each_source_keeps_its_own_word():
    assert eligible(said("PENDING", at=T1, event=True),
                    said("COMPLETED", at=T2, event=True),
                    said("PENDING", at=T1, source="merchant")) == ["COMPLETED", "PENDING"]


def test_a_stale_event_no_longer_raises_a_status_mismatch():
    """PayPal's early PENDING event, its later COMPLETED read and the merchant's COMPLETED row
    agree once only PayPal's current word counts."""
    current = current_word(*_split(said("PENDING", at=T1, event=True),
                                   said("COMPLETED", at=T2),
                                   said("COMPLETED", at=T2, source="merchant")))
    found = engine.run(snapshot([
        observation(str(o.id), "e1", o.source_id, o.normalized_value,
                    prop="finops:lifecycleStatus", currency=None, context="current")
        for o in current
    ]))
    assert found == []


def _split(*said_: tuple[SimpleNamespace, Record]) -> tuple[list, dict]:
    return [o for o, _ in said_], {record.id: record for _, record in said_}
