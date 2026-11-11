"""Financial Operations domain engine (spec 7.3, 7.4, 7.5).

Core hands this a plain, sorted snapshot of one run's entities, observations, resolved views
and relationship instances, and gets finding dicts back. Two separate stages:

- INTRA_ENTITY compares observations of one entity and property made by different sources.
  Values are compared only within one currency, unit, value basis and time context
  (AC-REC-13, AC-REC-15).
- INTER_ENTITY checks invariants across entities: a settlement batch against its bank credit
  under the OPEN-10 treatment, and duplicate successful captures for one order (AC-REC-14).

Money is computed only from RESOLVED views, in Money. Whatever is unresolved is not guessed: the
invariant is simply not evaluated. Equal amounts never make a duplicate (AC-PLY-02).
"""

from collections import defaultdict
from decimal import Decimal
from typing import Any

from packs.finops.money import Money, MoneyError
from packs.finops.settlement import SettlementLine, settlement_variance, treatment

F = "finops:"
COMPARED = {
    f"{F}lifecycleStatus": "STATUS_MISMATCH",
    f"{F}amount": "AMOUNT_MISMATCH",
    f"{F}feeAmount": "FEE_MISMATCH",
}
SUCCESSFUL_CAPTURE = "COMPLETED"


class _Index:
    def __init__(self, snapshot: dict[str, Any]) -> None:
        self.classes = {e["id"]: e["class_iri"] for e in snapshot["entities"]}
        self.observations: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for observation in snapshot["observations"]:
            key = (observation["entity_id"], observation["property_iri"])
            self.observations[key].append(observation)
        self.views = {(v["entity_id"], v["property_iri"]): v for v in snapshot["views"]}
        self.relationships: dict[str, list[dict]] = defaultdict(list)
        for relationship in snapshot["relationships"]:
            self.relationships[relationship["predicate"]].append(relationship)

    def of_class(self, class_iri: str) -> list[str]:
        return sorted(e for e, c in self.classes.items() if c == class_iri)

    def subjects(self, predicate: str, obj: str) -> list[dict]:
        return [r for r in self.relationships[predicate] if r["object"] == obj]

    def objects(self, predicate: str, subject: str) -> list[dict]:
        return [r for r in self.relationships[predicate] if r["subject"] == subject]

    def resolved(self, entity: str, property_iri: str) -> dict | None:
        view = self.views.get((entity, property_iri))
        return view if view and view["status"] == "RESOLVED" else None


def _comparable_key(observation: dict) -> tuple:
    return (observation["currency"], observation["unit"], observation["value_basis"],
            observation["temporal_context"])


def _value(observation: dict) -> Any:
    return Decimal(observation["numeric"]) if observation["numeric"] is not None else (
        observation["value"]
    )


def _intra(index: _Index) -> list[dict]:
    findings = []
    for (entity, property_iri), observations in sorted(index.observations.items()):
        case_type = COMPARED.get(property_iri)
        if case_type is None:
            continue
        groups: dict[tuple, list[dict]] = defaultdict(list)
        for observation in observations:
            if observation["value"] is not None:
                groups[_comparable_key(observation)].append(observation)
        for (currency, unit, basis, context), group in sorted(groups.items(), key=str):
            if len({o["source_id"] for o in group}) < 2 or len({_value(o) for o in group}) < 2:
                continue
            view = index.views.get((entity, property_iri), {})
            numeric = all(o["numeric"] is not None for o in group)
            authoritative = view.get("numeric") if view.get("status") == "RESOLVED" else None
            others = sorted({o["numeric"] for o in group if numeric} - {authoritative})
            amounts = numeric and authoritative is not None and len(others) == 1 and currency
            findings.append({
                "case_type": case_type,
                "scope": "INTRA_ENTITY",
                "entity_ids": [entity],
                "property_iris": [property_iri],
                "observation_ids": sorted(o["id"] for o in group),
                "expected": view.get("value") if view.get("status") == "RESOLVED" else None,
                "observed": sorted(
                    ({"source_id": o["source_id"], "value": o["value"], "currency": o["currency"],
                      "observation_id": o["id"]} for o in group),
                    key=lambda item: item["observation_id"],
                ),
                "expected_amount": authoritative if amounts else None,
                "observed_amount": others[0] if amounts else None,
                "currency": currency if amounts else None,
                "method": "finops.intra_entity.compare/1",
                "evidence": {
                    "comparability": {"currency": currency, "unit": unit, "value_basis": basis,
                                      "temporal_context": context},
                    "resolution": view.get("status", "UNRESOLVED"),
                    "authority_source_id": view.get("authority_source_id"),
                },
            })
    return findings


def _money(view: dict | None) -> Money | None:
    if view is None or view["numeric"] is None or view["currency"] is None:
        return None
    return Money.parse(view["numeric"], view["currency"])


def _optional_money(index: _Index, entity: str, property_iri: str) -> tuple[bool, Money | None]:
    """(usable, value): a present but unresolved value makes the entry unusable."""
    if (entity, property_iri) not in index.views:
        return True, None
    view = index.resolved(entity, property_iri)
    value = _money(view)
    return value is not None, value


def _view_ids(index: _Index, entity: str, properties: list[str]) -> list[str]:
    ids: set[str] = set()
    for property_iri in properties:
        view = index.views.get((entity, property_iri))
        if view:
            ids.update(view["observation_ids"])
    return sorted(ids)


def _settlement(index: _Index) -> list[dict]:
    findings = []
    entry_properties = [f"{F}entryType", f"{F}amount", f"{F}feeAmount", f"{F}netAmount"]
    for batch in index.of_class(f"{F}SettlementBatch"):
        included = index.subjects(f"{F}includedInSettlement", batch)
        settled = index.objects(f"{F}settledThrough", batch)
        if not included or len(settled) != 1:
            continue
        bank = settled[0]["object"]
        bank_credit = _money(index.resolved(bank, f"{F}amount"))
        if bank_credit is None:
            continue
        lines, usable = [], True
        for relationship in included:
            entry = relationship["subject"]
            entry_type = index.resolved(entry, f"{F}entryType")
            gross = _money(index.resolved(entry, f"{F}amount"))
            fee_ok, fee = _optional_money(index, entry, f"{F}feeAmount")
            net_ok, net = _optional_money(index, entry, f"{F}netAmount")
            if entry_type is None or gross is None or not (fee_ok and net_ok):
                usable = False
                break
            lines.append(SettlementLine(entry, entry_type["value"], gross, fee, net))
        if not usable:
            continue
        try:
            result = settlement_variance(lines, bank_credit)
        except MoneyError:
            continue  # different currencies are never compared (AC-REC-15)
        if result.variance.amount == 0:
            continue
        entries = sorted(r["subject"] for r in included)
        observations = _view_ids(index, bank, [f"{F}amount"])
        for entry in entries:
            observations += _view_ids(index, entry, entry_properties)
        findings.append({
            "case_type": "SETTLEMENT_VARIANCE",
            "scope": "INTER_ENTITY",
            "entity_ids": [batch, bank, *entries],
            "property_iris": [f"{F}amount", f"{F}netAmount", f"{F}feeAmount"],
            "observation_ids": sorted(set(observations)),
            "relationship_ids": sorted(r["id"] for r in [*included, *settled]),
            "expected": {"expected_net": format(result.expected_net.amount, "f")},
            "observed": {"bank_credit": format(bank_credit.amount, "f")},
            "expected_amount": format(result.expected_net.amount, "f"),
            "observed_amount": format(bank_credit.amount, "f"),
            "currency": bank_credit.currency,
            "method": "finops.settlement_variance/1",
            "evidence": {
                "variance": format(result.variance.amount, "f"),
                "included_entries": result.included,
                "excluded_entries": result.excluded,
                "unclassified_entries": result.unclassified,
                "treatment_version": treatment()["version"],
            },
        })
    return findings


def _references(index: _Index, entity: str) -> list[str]:
    return sorted({o["value"] for o in index.observations[(entity, f"{F}providerReference")]
                   if o["value"]})


def _duplicates(index: _Index) -> list[dict]:
    findings = []
    for order in index.of_class(f"{F}Order"):
        links = [r for r in index.subjects(f"{F}forOrder", order)
                 if index.classes.get(r["subject"]) == f"{F}Capture"]
        successful = []
        for relationship in links:
            status = index.resolved(relationship["subject"], f"{F}lifecycleStatus")
            if status is not None and status["value"] == SUCCESSFUL_CAPTURE:
                successful.append(relationship)
        captures = sorted(r["subject"] for r in successful)
        references = {capture: _references(index, capture) for capture in captures}
        if len(captures) < 2 or len({tuple(refs) for refs in references.values()}) < 2:
            continue
        findings.append({
            "case_type": "DUPLICATE_CAPTURE",
            "scope": "INTER_ENTITY",
            "entity_ids": [order, *captures],
            "property_iris": [f"{F}lifecycleStatus", f"{F}providerReference"],
            "observation_ids": sorted({
                i for capture in captures
                for i in _view_ids(index, capture, [f"{F}lifecycleStatus"])
            }),
            "relationship_ids": sorted(r["id"] for r in successful),
            "expected": {"successful_captures": 1},
            "observed": {
                "successful_captures": len(captures),
                "capture_references": sorted(r for refs in references.values() for r in refs),
            },
            "method": "finops.duplicate_capture/1",
            "evidence": {
                "rule": "two or more distinct successful captures for one intended order; "
                        "amounts are not considered",
            },
        })
    return findings


def run(snapshot: dict[str, Any]) -> list[dict]:
    index = _Index(snapshot)
    return [*_intra(index), *_settlement(index), *_duplicates(index)]
