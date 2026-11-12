"""SaaS Subscription Lifecycle semantic validators, run for each publication candidate (POL-12).

Each takes the candidate's rdflib graph and returns violation messages; an empty list passes.
"""

from rdflib import Graph, Namespace

SAAS = Namespace("urn:settleiq:pack:saas#")

_REQUIRED_TERMS = (
    "Account", "Subscription", "Invoice", "subscriptionReference", "invoiceReference",
    "status", "currentPeriodEnd", "periodEnd", "invoiceFor",
)


def check_required_terms(graph: Graph) -> list[str]:
    subjects = set(graph.subjects())
    return [
        f"Required SaaS term is missing: {name}"
        for name in _REQUIRED_TERMS
        if SAAS[name] not in subjects
    ]
