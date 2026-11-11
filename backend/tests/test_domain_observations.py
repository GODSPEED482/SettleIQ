"""Property observations and resolved property views (task 3.2, spec 5.5, 5.6).

AC-REC-10 two bindings give two observations; AC-REC-12 authority applies without dropping
or overwriting anything; AC-REC-16 missing, overlapping or disputed authority stays explicit.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import scoped_tx
from app.sources.evidence import read_verified
from tests.domain_fixture import AUTHORITY, EVIDENCE_ROOT, FinanceTenant

pytestmark = pytest.mark.db

CAPTURE = {"id": "CAP-1", "invoice_id": "ORD-1", "status": "COMPLETED",
           "amount": {"value": "100.00", "currency_code": "USD"}}
PAYMENT = {"paypal_capture_id": "CAP-1", "order_id": "ORD-1", "state": "pending",
           "amount": "100.00", "currency": "USD"}


def capture_view(member: FinanceTenant) -> tuple[dict, dict[str, dict]]:
    (capture,) = member.entities("finops:Capture")
    entity = member.get(f"/api/entities/{capture['id']}")
    return entity, {view["property_iri"]: view for view in entity["resolved"]}


def observed(entity: dict, property_iri: str) -> list[dict]:
    return [o for o in entity["observations"] if o["property_iri"] == property_iri]


@pytest.fixture(scope="module")
def resolved() -> tuple[FinanceTenant, dict, dict]:
    member = FinanceTenant()
    member.ingest("paypal", "captures", CAPTURE)
    member.ingest("merchant", "payments", PAYMENT)
    member.run()
    entity, views = capture_view(member)
    return member, entity, views


def test_two_bindings_to_one_property_give_two_observations(resolved):
    member, entity, _ = resolved
    amounts = observed(entity, "finops:amount")
    assert len(amounts) == 2
    assert {o["source_id"] for o in amounts} == {
        str(member.sources["paypal"]), str(member.sources["merchant"])
    }
    assert len({o["mapping_id"] for o in amounts}) == 2
    for observation in amounts:
        assert observation["numeric_value"] == "100.000000"
        assert observation["currency"] == "USD"
        assert observation["evidence_hash"]


def test_observations_carry_raw_and_normalized_values(resolved):
    _, entity, _ = resolved
    statuses = {o["raw_value"]: o["normalized_value"]
                for o in observed(entity, "finops:lifecycleStatus")}
    assert statuses == {"COMPLETED": "COMPLETED", "pending": "PENDING"}
    # Provenance comes from the raw evidence, so the UI can label reference data.
    assert {o["provenance"] for o in entity["observations"]} == {"synthetic_reference"}


def test_authority_resolves_without_dropping_other_observations(resolved):
    _, entity, views = resolved
    status = views["finops:lifecycleStatus"]
    assert (status["status"], status["value"]) == ("RESOLVED", "COMPLETED")
    every = {o["id"] for o in observed(entity, "finops:lifecycleStatus")}
    assert set(status["contributing_observation_ids"]) == every
    assert len(every) == 2  # the merchant's PENDING is kept, not overwritten


def test_raw_evidence_is_never_overwritten(resolved):
    member, entity, _ = resolved
    with scoped_tx(member.tenant) as conn:
        for observation in entity["observations"]:
            data = read_verified(conn, observation["raw_evidence_id"], root=EVIDENCE_ROOT)
            assert data  # the stored bytes still match their hash
        attributes = conn.execute(
            text("SELECT attributes FROM source_records WHERE source_id = :s"),
            {"s": member.sources["merchant"]},
        ).scalar_one()
    assert attributes["state"] == "pending"


def test_a_property_without_authority_is_unresolved(resolved):
    _, _, views = resolved
    assert views["finops:orderReference"]["status"] == "UNRESOLVED"
    assert views["finops:orderReference"]["value"] is None


@pytest.mark.parametrize("statement", ["UPDATE property_observations SET normalized_value = 'x'",
                                       "DELETE FROM property_observations"])
def test_observations_are_append_only(resolved, statement):
    member, _, _ = resolved
    with pytest.raises(DBAPIError, match="permission denied"):
        with scoped_tx(member.tenant) as conn:
            conn.execute(text(statement))


def test_overlapping_authority_is_an_explicit_conflict():
    member = FinanceTenant(
        authority=AUTHORITY + [("finops:lifecycleStatus", "finops:Capture", "merchant")]
    )
    member.ingest("paypal", "captures", CAPTURE)
    member.ingest("merchant", "payments", PAYMENT)
    member.run()
    _, views = capture_view(member)
    assert views["finops:lifecycleStatus"]["status"] == "AUTHORITY_CONFLICT"
    assert views["finops:lifecycleStatus"]["value"] is None


def test_expired_authority_is_stale():
    expired = [a for a in AUTHORITY if a[0] != "finops:lifecycleStatus"] + [
        ("finops:lifecycleStatus", "finops:Capture", "paypal", "2026-02-01T00:00:00Z")
    ]
    member = FinanceTenant(authority=expired)
    member.ingest("paypal", "captures", CAPTURE)
    member.run()
    _, views = capture_view(member)
    assert views["finops:lifecycleStatus"]["status"] == "STALE"


def test_disagreeing_authoritative_observations_are_a_conflict_not_the_latest():
    member = FinanceTenant()
    member.ingest("paypal", "captures", CAPTURE | {"status": "PENDING"})
    member.ingest("paypal", "captures", CAPTURE)  # same capture, newer payload
    member.run()
    _, views = capture_view(member)
    assert views["finops:lifecycleStatus"]["status"] == "CONFLICT"
    assert views["finops:lifecycleStatus"]["value"] is None
    assert len(views["finops:lifecycleStatus"]["contributing_observation_ids"]) == 2


def test_an_entity_in_identity_review_waits_for_review():
    member = FinanceTenant()
    member.ingest("merchant", "orders", {"order_id": "ORD-1", "paypal_order_id": "PO-1"})
    member.ingest("merchant", "orders", {"order_id": "ORD-2", "paypal_order_id": "PO-2"})
    member.run()
    member.ingest("merchant", "orders", {"order_id": "ORD-1", "paypal_order_id": "PO-2"})
    member.run()
    for order in member.entities("finops:Order"):
        entity = member.get(f"/api/entities/{order['id']}")
        assert {view["status"] for view in entity["resolved"]} == {"HUMAN_REVIEW"}


def test_each_run_keeps_its_own_views():
    member = FinanceTenant()
    member.ingest("paypal", "captures", CAPTURE)
    first = member.run()
    second = member.run()
    with scoped_tx(member.tenant) as conn:
        counts = conn.execute(
            text("SELECT run_id, count(*) FROM resolved_property_views GROUP BY run_id")
        ).all()
    assert {str(run_id) for run_id, _ in counts} == {first["id"], second["id"]}
