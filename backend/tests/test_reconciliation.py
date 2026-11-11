"""The Finance engine and the G3 exit criteria (tasks 3.3, 3.10, spec 7.3-7.5, 17).

Exit: identical version-pinned inputs give identical findings; fees already in net are never
subtracted again (AC-REC-07); an ambiguous identity match is never forced (AC-REC-06).
"""

from decimal import Decimal

import pytest
from sqlalchemy import text

from app.db import scoped_tx
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
