"""SaaS Subscription Lifecycle domain engine (spec 14.4).

Core hands this the same plain snapshot it hands the Finance engine and gets finding dicts back.
One inter-entity check, RENEWAL_INVOICE_MISMATCH, in two forms:

- RENEWED_WITHOUT_PAID_INVOICE: an ACTIVE subscription's current period has no PAID invoice.
- PAID_WITHOUT_RENEWAL: a PAID invoice covers a period ending after the subscription's current
  period, so the customer paid and the subscription was never extended.

Only RESOLVED views are read. A subscription with any unresolved input is not evaluated: a
missing value is never read as a missing invoice. The pack computes no money.
"""

from collections import defaultdict
from datetime import datetime
from typing import Any

S = "saas:"
CASE_TYPE = "RENEWAL_INVOICE_MISMATCH"
ACTIVE = "ACTIVE"
PAID = "PAID"


class _Index:
    def __init__(self, snapshot: dict[str, Any]) -> None:
        self.classes = {e["id"]: e["class_iri"] for e in snapshot["entities"]}
        self.views = {(v["entity_id"], v["property_iri"]): v for v in snapshot["views"]}
        self.relationships: dict[str, list[dict]] = defaultdict(list)
        for relationship in snapshot["relationships"]:
            self.relationships[relationship["predicate"]].append(relationship)

    def of_class(self, class_iri: str) -> list[str]:
        return sorted(e for e, c in self.classes.items() if c == class_iri)

    def subjects(self, predicate: str, obj: str) -> list[dict]:
        return sorted((r for r in self.relationships[predicate] if r["object"] == obj),
                      key=lambda r: r["subject"])

    def resolved(self, entity: str, property_iri: str) -> str | None:
        view = self.views.get((entity, property_iri))
        if view is None or view["status"] != "RESOLVED" or view["value"] is None:
            return None
        return view["value"]

    def observation_ids(self, entity: str, properties: list[str]) -> list[str]:
        ids: set[str] = set()
        for property_iri in properties:
            view = self.views.get((entity, property_iri))
            if view:
                ids.update(view["observation_ids"])
        return sorted(ids)


def _moment(value: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


SUBSCRIPTION_PROPERTIES = [f"{S}status", f"{S}currentPeriodEnd"]
INVOICE_PROPERTIES = [f"{S}status", f"{S}periodEnd"]


def _finding(index: _Index, subscription: str, links: list[dict], rule: str,
             expected: dict, observed: dict) -> dict[str, Any]:
    invoices = [r["subject"] for r in links]
    observations = index.observation_ids(subscription, SUBSCRIPTION_PROPERTIES)
    for invoice in invoices:
        observations += index.observation_ids(invoice, INVOICE_PROPERTIES)
    return {
        "case_type": CASE_TYPE,
        "scope": "INTER_ENTITY",
        "entity_ids": [subscription, *invoices],
        "property_iris": [f"{S}status", f"{S}currentPeriodEnd", f"{S}periodEnd"],
        "observation_ids": sorted(set(observations)),
        "relationship_ids": sorted(r["id"] for r in links),
        "expected": expected,
        "observed": observed,
        "method": "saas.renewal_invoice/1",
        "evidence": {"rule": rule},
    }


def _check(index: _Index, subscription: str) -> list[dict[str, Any]]:
    status = index.resolved(subscription, f"{S}status")
    period = index.resolved(subscription, f"{S}currentPeriodEnd")
    period_end = _moment(period) if period else None
    if status is None or period_end is None:
        return []
    links = index.subjects(f"{S}invoiceFor", subscription)
    invoices = []
    for link in links:
        invoice_status = index.resolved(link["subject"], f"{S}status")
        invoice_period = index.resolved(link["subject"], f"{S}periodEnd")
        invoice_end = _moment(invoice_period) if invoice_period else None
        if invoice_status is None or invoice_end is None:
            return []  # one unresolved invoice could be the paid one: do not guess
        invoices.append((link["subject"], invoice_status, invoice_end))
    paid = [(invoice, end) for invoice, invoice_status, end in invoices if invoice_status == PAID]

    findings = []
    if status == ACTIVE and not any(end == period_end for _, end in paid):
        findings.append(_finding(
            index, subscription, links, "RENEWED_WITHOUT_PAID_INVOICE",
            expected={"paid_invoice_for_period_end": period},
            observed={"subscription_status": status, "invoices": [
                {"invoice_id": invoice, "status": invoice_status, "period_end": end.isoformat()}
                for invoice, invoice_status, end in invoices
            ]},
        ))
    ahead = [(invoice, end) for invoice, end in paid if end > period_end]
    if ahead:
        findings.append(_finding(
            index, subscription, links, "PAID_WITHOUT_RENEWAL",
            expected={"current_period_end_at_least": max(end for _, end in ahead).isoformat()},
            observed={"current_period_end": period, "paid_invoices": [
                {"invoice_id": invoice, "period_end": end.isoformat()} for invoice, end in ahead
            ]},
        ))
    return findings


def run(snapshot: dict[str, Any]) -> list[dict]:
    index = _Index(snapshot)
    findings = []
    for subscription in index.of_class(f"{S}Subscription"):
        findings += _check(index, subscription)
    return findings
