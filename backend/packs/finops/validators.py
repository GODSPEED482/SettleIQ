"""Financial Operations semantic validators, run for each publication candidate (POL-12).

Each takes the candidate's rdflib graph and returns violation messages; an empty list passes.
"""

from rdflib import OWL, RDFS, Graph, Namespace, URIRef

FINOPS = Namespace("urn:settleiq:pack:finops#")

_REQUIRED_TERMS = (
    "Order", "Payment", "Capture", "Refund", "SettlementBatch", "SettlementEntry",
    "BankTransaction", "generatesCapture", "includedInSettlement", "settledThrough",
)


def _is_subclass(graph: Graph, child: URIRef, parent: URIRef) -> bool:
    return parent in graph.transitive_objects(child, RDFS.subClassOf)


def check_settlement_cardinality(graph: Graph) -> list[str]:
    """SettlementBatch must stay distinct from SettlementEntry (spec 5.2)."""
    batch, entry = FINOPS.SettlementBatch, FINOPS.SettlementEntry
    equivalent = (batch, OWL.equivalentClass, entry) in graph or (
        entry, OWL.equivalentClass, batch
    ) in graph
    if equivalent or _is_subclass(graph, batch, entry) or _is_subclass(graph, entry, batch):
        return ["SettlementBatch and SettlementEntry must remain distinct classes."]
    return []


def check_required_terms(graph: Graph) -> list[str]:
    subjects = set(graph.subjects())
    return [
        f"Required Finance term is missing: {name}"
        for name in _REQUIRED_TERMS
        if FINOPS[name] not in subjects
    ]
