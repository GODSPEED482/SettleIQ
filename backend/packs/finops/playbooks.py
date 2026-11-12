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


REFERENCE = f"{F}providerReference"
OCCURRED = f"{F}occurredAt"
CENT = Decimal("0.01")


def _class(snapshot: dict, entity: str) -> str | None:
    return next((e["class_iri"] for e in snapshot["entities"] if e["id"] == entity), None)


def _linked(snapshot: dict, predicate: str, obj: str) -> list[str]:
    return sorted({r["subject"] for r in snapshot["relationships"]
                   if r["predicate"] == predicate and r["object"] == obj})


def duplicate_capture_refund(snapshot: dict[str, Any], parameters: dict[str, Any]) -> dict:
    """PB-002: refund the later of two distinct successful PayPal captures for one order.

    Equal amounts are necessary but never proof (AC-PLY-02): the proof is two distinct capture
    references, both authoritatively COMPLETED by PayPal, both linked to one order the merchant
    knows, with the original told apart from the duplicate by PayPal's own capture time, and no
    refund or dispute already touching that order.
    """
    case = snapshot["case"]
    if case["case_type"] != "DUPLICATE_CAPTURE":
        return _result(["the case is not a duplicate capture"])
    orders = [e for e in case["entity_ids"] if _class(snapshot, e) == f"{F}Order"]
    if len(orders) != 1:
        return _result(["the case must name exactly one order"])
    [order] = orders
    captures = sorted(e for e in case["entity_ids"] if _class(snapshot, e) == f"{F}Capture")
    if len(captures) != 2:
        return _result(["exactly two captures are needed to tell original from duplicate"])

    failures: list[str] = []
    used: list[dict] = []
    linked = set(_linked(snapshot, f"{F}forOrder", order))
    facts = {}
    for capture in captures:
        view = _view(snapshot, capture, STATUS)
        authority = view and snapshot["sources"].get(view["authority_source_id"] or "")
        reference = _latest(snapshot, capture, REFERENCE, {PAYPAL})
        amount = _latest(snapshot, capture, AMOUNT, {PAYPAL})
        occurred = _latest(snapshot, capture, OCCURRED, {PAYPAL})
        used += [o for o in (reference, amount, occurred) if o]
        if capture not in linked:
            failures.append(f"capture {capture} is not linked to the order")
        if not view or view["status"] != "RESOLVED" or view["value"] != "COMPLETED" or (
                authority != PAYPAL):
            failures.append(f"capture {capture} is not authoritatively COMPLETED by PayPal")
        if reference is None or amount is None or occurred is None:
            failures.append(f"capture {capture} lacks PayPal's reference, amount or time")
            continue
        facts[capture] = (occurred["value"], reference["value"], amount)
    if failures:
        return _result(failures, evidence_ids=[o["id"] for o in used])

    (first, second) = (facts[c] for c in captures)
    if first[1] == second[1]:
        failures.append("the two captures share a reference, so they are one capture")
    if first[0] == second[0]:
        failures.append("PayPal's capture times cannot tell the original from the duplicate")
    if first[2]["currency"] != second[2]["currency"] or (
            Decimal(first[2]["numeric"]) != Decimal(second[2]["numeric"])):
        failures.append("the captures differ in amount or currency, so neither is a duplicate")

    merchant_order = _latest(snapshot, order, ORDER, MERCHANT_CONNECTORS)
    if merchant_order is None:
        failures.append("the merchant does not know this order")
    else:
        used.append(merchant_order)
    refunds = [e for e in _linked(snapshot, f"{F}forOrder", order)
               if _class(snapshot, e) == f"{F}Refund"]
    refunds += [r["subject"] for r in snapshot["relationships"]
                if r["predicate"] == f"{F}refundsCapture" and r["object"] in captures]
    if refunds:
        failures.append("a refund already exists for this order or its captures")
    disputes = [e["id"] for e in snapshot["entities"] if e["class_iri"] == f"{F}Chargeback"]
    if disputes:
        failures.append("a dispute or chargeback is linked to this order")
    if failures:
        return _result(failures, evidence_ids=[o["id"] for o in used])

    original, duplicate = sorted(facts.values(), key=lambda fact: fact[0])
    amount = duplicate[2]
    value = Decimal(amount["numeric"])
    if value <= 0 or value != value.quantize(CENT):
        return _result(["the duplicate's amount is not a positive two-decimal value"],
                       evidence_ids=[o["id"] for o in used])
    return _result(
        [],
        target={
            "paypal_source_id": amount["source_id"],
            "capture_id": duplicate[1],
            "amount": format(value.quantize(CENT), "f"),
            "currency": amount["currency"],
        },
        proof={
            "order_reference": merchant_order["value"],
            "original": {"capture_id": original[1], "captured_at": original[0]},
            "duplicate": {"capture_id": duplicate[1], "captured_at": duplicate[0]},
            "rule": "two distinct PayPal captures, both COMPLETED, for one merchant order; "
                    "the later is the duplicate; no refund or dispute exists",
        },
        evidence_ids=[o["id"] for o in used] + [o["source_record_id"] for o in used],
    )
