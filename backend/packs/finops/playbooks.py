"""Finance playbook preconditions (spec 9.2). Core calls these through the pack registry.

Each takes a case evidence snapshot (app/playbooks/evidence.py) and the request parameters and
returns `{"eligible", "failures", "target", "proof", "evidence_ids"}`. They decide only from
observations, resolved views and relationships already in the snapshot: they never read a
provider, never compute money beyond comparing recorded values, and never guess. Anything
missing or ambiguous is a failure, so the playbook is ineligible (AC-PLY-01).
"""

from decimal import Decimal
from typing import Any

F = "finops:"
PAYPAL = "paypal"
MERCHANT_CONNECTORS = {"merchant-postgres", "internal-merchant"}
STATUS = f"{F}lifecycleStatus"
AMOUNT = f"{F}amount"
ORDER = f"{F}orderReference"


def _result(failures: list[str], target: dict | None = None, proof: dict | None = None,
            evidence_ids: list[str] | None = None) -> dict[str, Any]:
    eligible = not failures
    return {
        "eligible": eligible,
        "failures": failures,
        "target": target if eligible else None,
        "proof": proof or {},
        "evidence_ids": sorted(set(evidence_ids or [])),
    }


def _latest(snapshot: dict, entity: str, property_iri: str, connectors: set[str]) -> dict | None:
    """The most recently observed value of one property from sources of these connectors."""
    sources = snapshot["sources"]
    held = [o for o in snapshot["observations"]
            if o["entity_id"] == entity and o["property_iri"] == property_iri
            and sources.get(o["source_id"]) in connectors]
    return max(held, key=lambda o: (o["observed_at"], o["id"])) if held else None


def _view(snapshot: dict, entity: str, property_iri: str) -> dict | None:
    for view in snapshot["views"]:
        if view["entity_id"] == entity and view["property_iri"] == property_iri:
            return view
    return None


def payment_state_recovery(snapshot: dict[str, Any], parameters: dict[str, Any]) -> dict:
    """PB-001: PayPal says the capture COMPLETED, the merchant payment is still PENDING.

    Spec 4.4.1: the action is the one conditional transition PENDING -> COMPLETED on the
    merchant's own payment row, never a replayed event.
    """
    case = snapshot["case"]
    failures: list[str] = []
    if case["case_type"] != "STATUS_MISMATCH" or STATUS not in case["property_iris"]:
        return _result(["the case is not a lifecycle status mismatch"])
    if len(case["entity_ids"]) != 1:
        return _result(["the case must concern exactly one payment"])
    [entity] = case["entity_ids"]

    view = _view(snapshot, entity, STATUS)
    authority = view and snapshot["sources"].get(view["authority_source_id"] or "")
    if not view or view["status"] != "RESOLVED" or view["value"] != "COMPLETED":
        failures.append("PayPal's capture status is not authoritatively COMPLETED")
    elif authority != PAYPAL:
        failures.append("the authoritative status does not come from PayPal")

    provider = _latest(snapshot, entity, STATUS, {PAYPAL})
    merchant = _latest(snapshot, entity, STATUS, MERCHANT_CONNECTORS)
    if provider is None or provider["value"] != "COMPLETED":
        failures.append("no PayPal observation of a COMPLETED capture")
    if merchant is None or merchant["value"] != "PENDING":
        failures.append("the merchant payment is not PENDING")

    provider_amount = _latest(snapshot, entity, AMOUNT, {PAYPAL})
    merchant_amount = _latest(snapshot, entity, AMOUNT, MERCHANT_CONNECTORS)
    if provider_amount is None or merchant_amount is None:
        failures.append("amount is not observed by both PayPal and the merchant")
    elif (provider_amount["currency"] != merchant_amount["currency"]
          or Decimal(provider_amount["numeric"]) != Decimal(merchant_amount["numeric"])):
        failures.append("amount or currency disagree between PayPal and the merchant")

    provider_order = _latest(snapshot, entity, ORDER, {PAYPAL})
    merchant_order = _latest(snapshot, entity, ORDER, MERCHANT_CONNECTORS)
    if (provider_order is None or merchant_order is None
            or provider_order["value"] != merchant_order["value"]):
        failures.append("the order relationship does not agree")

    used = [o for o in (provider, merchant, provider_amount, merchant_amount, provider_order,
                        merchant_order) if o is not None]
    if failures:
        return _result(failures, evidence_ids=[o["id"] for o in used])
    return _result(
        [],
        target={
            "merchant_source_id": merchant["source_id"],
            "payment_id": merchant["external_id"],
            "expected_current_status": "PENDING",
            "requested_status": "COMPLETED",
        },
        proof={
            "provider_status": provider["id"],
            "merchant_status": merchant["id"],
            "amount": provider_amount["numeric"],
            "currency": provider_amount["currency"],
            "order_reference": provider_order["value"],
        },
        evidence_ids=[o["id"] for o in used] + [o["source_record_id"] for o in used],
    )
