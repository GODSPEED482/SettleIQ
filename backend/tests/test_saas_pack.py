"""The SaaS Subscription Lifecycle reference pack (task 6.1, spec 14.4, AC-OPEN-03, AC-OPEN-07).

The pack loads, validates, activates and runs its SYNTHETIC fixture through the same generic
pipeline as Financial Operations, with no platform-core change. It runs alone, and beside
finops in one tenant, where each pack's run sees only its own classes.
"""

import csv
import hashlib
import io
from typing import Any

import pytest
import yaml

from app.domain_packs.manifest import PACKS_DIR, load_domain_packs
from app.domain_packs.registry import assert_can_activate, resolve_pack_entrypoints
from packs.saas_subscription import engine
from tests.domain_fixture import (
    AUTHORITY,
    BINDINGS,
    IDENTIFIER,
    STATUS,
    FinanceTenant,
    seed_reconciliation,
)

PACK = PACKS_DIR / "saas_subscription"
MANIFEST = PACK / "fixtures" / "SYNTHETIC_REFERENCE_saas_v1.manifest.yaml"

MOMENT = {"datatype": "datetime", "value_basis": "timestamp", "currency_field": None,
          "temporal_context": "current", "transform": {"name": "iso8601"}}
UPPER = STATUS | {"transform": {"name": "upper"}}

# The billing database's two tables, bound the way a tenant would bind them in the Studio.
SAAS_BINDINGS = [
    ("billing", "subscriptions", "saas:Subscription", [
        ("$.subscription_id", "saas:subscriptionReference", IDENTIFIER),
        ("$.account_id", "saas:accountReference", IDENTIFIER),
        ("$.status", "saas:status", UPPER),
        ("$.current_period_end", "saas:currentPeriodEnd", MOMENT),
    ]),
    ("billing", "invoices", "saas:Invoice", [
        ("$.invoice_id", "saas:invoiceReference", IDENTIFIER),
        ("$.subscription_id", "saas:subscriptionReference", IDENTIFIER),
        ("$.status", "saas:status", UPPER),
        ("$.period_end", "saas:periodEnd", MOMENT),
    ]),
]
SAAS_OBJECTS = {"subscriptions": "TABLE", "invoices": "TABLE"}
# The billing database is the authority for its own subscription and invoice state.
SAAS_AUTHORITY = [
    ("saas:status", "saas:Subscription", "billing"),
    ("saas:currentPeriodEnd", "saas:Subscription", "billing"),
    ("saas:status", "saas:Invoice", "billing"),
    ("saas:periodEnd", "saas:Invoice", "billing"),
]


def manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))


def fixture_rows(kind: str) -> list[dict[str, str]]:
    path = MANIFEST.parent / manifest()["files"][kind]["file"]
    return list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8"))))


def seed_saas(member: FinanceTenant) -> None:
    for row in fixture_rows("subscriptions"):
        member.ingest("billing", "subscriptions", row, external_id=row["subscription_id"])
    for row in fixture_rows("invoices"):
        member.ingest("billing", "invoices", row, external_id=row["invoice_id"])


def saas_tenant(packs: tuple[str, ...] = ("saas",)) -> FinanceTenant:
    finance = "finops" in packs
    return FinanceTenant(
        packs=packs,
        sources=("paypal", "merchant", "report", "bank", "billing") if finance else ("billing",),
        bindings=(BINDINGS if finance else []) + SAAS_BINDINGS,
        authority=(AUTHORITY if finance else []) + SAAS_AUTHORITY,
        object_types=SAAS_OBJECTS,
    )


def rules(cases: list[dict]) -> dict[str, dict]:
    return {case["evidence"]["finding"]["rule"]: case for case in cases
            if case["case_type"] == "RENEWAL_INVOICE_MISMATCH"}


# Unit: the manifest, the fixture and the engine


def test_the_pack_loads_and_its_code_resolves():
    pack = load_domain_packs()["saas"]
    resolve_pack_entrypoints({"saas": pack})
    assert pack.domain_engine is not None
    assert [case.code for case in pack.case_types] == ["RENEWAL_INVOICE_MISMATCH"]


def test_the_pack_declares_no_state_changing_action():
    pack = load_domain_packs()["saas"]
    assert pack.connector_action_capabilities == []
    assert pack.playbook_templates == []


def test_the_pack_activates_alone_and_beside_finops():
    packs = load_domain_packs()
    assert_can_activate(packs["saas"], [])
    assert_can_activate(packs["saas"], [packs["finops"]])
    assert_can_activate(packs["finops"], [packs["saas"]])


def test_fixture_files_match_their_checksums_and_are_listed():
    data = manifest()
    assert data["provenance"] == "synthetic_reference"
    for item in data["files"].values():
        path = MANIFEST.parent / item["file"]
        assert "SYNTHETIC" in path.name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]
    declared = {PACK / path for path in load_domain_packs()["saas"].test_fixtures}
    assert declared == {MANIFEST, *(MANIFEST.parent / f["file"] for f in data["files"].values())}


def _snapshot(subscription: dict[str, Any], invoices: list[dict[str, Any]]) -> dict[str, Any]:
    """A hand-built engine snapshot: one subscription and its invoices, every view RESOLVED
    unless a value is given as ("CONFLICT", value)."""
    entities, views, relationships = [], [], []

    def add(entity: str, class_iri: str, values: dict[str, Any]) -> None:
        entities.append({"id": entity, "class_iri": class_iri})
        for prop, value in values.items():
            status, value = value if isinstance(value, tuple) else ("RESOLVED", value)
            views.append({"entity_id": entity, "property_iri": f"saas:{prop}", "status": status,
                          "value": value, "numeric": None, "currency": None,
                          "authority_source_id": None, "observation_ids": [f"o-{entity}-{prop}"]})

    add("sub", "saas:Subscription", subscription)
    for number, invoice in enumerate(invoices):
        add(f"inv-{number}", "saas:Invoice", invoice)
        relationships.append({"id": f"r-{number}", "subject": f"inv-{number}",
                              "predicate": "saas:invoiceFor", "object": "sub"})
    return {"entities": entities, "views": views, "relationships": relationships,
            "observations": []}


DEC = "2026-12-01T00:00:00+00:00"
NOV = "2026-11-01T00:00:00+00:00"


def test_an_active_period_with_its_paid_invoice_raises_nothing():
    snapshot = _snapshot({"status": "ACTIVE", "currentPeriodEnd": DEC},
                         [{"status": "PAID", "periodEnd": NOV},
                          {"status": "PAID", "periodEnd": DEC}])
    assert engine.run(snapshot) == []


def test_an_active_period_without_a_paid_invoice_is_a_mismatch():
    [finding] = engine.run(_snapshot({"status": "ACTIVE", "currentPeriodEnd": DEC},
                                     [{"status": "OPEN", "periodEnd": DEC}]))
    assert finding["case_type"] == "RENEWAL_INVOICE_MISMATCH"
    assert finding["evidence"]["rule"] == "RENEWED_WITHOUT_PAID_INVOICE"
    assert finding["entity_ids"] == ["sub", "inv-0"]
    assert finding["relationship_ids"] == ["r-0"]


def test_a_paid_period_beyond_the_subscription_is_a_mismatch():
    [finding] = engine.run(_snapshot({"status": "PAST_DUE", "currentPeriodEnd": NOV},
                                     [{"status": "PAID", "periodEnd": DEC}]))
    assert finding["evidence"]["rule"] == "PAID_WITHOUT_RENEWAL"
    assert finding["expected"] == {"current_period_end_at_least": DEC}


@pytest.mark.parametrize("subscription, invoice", [
    ({"status": ("CONFLICT", "ACTIVE"), "currentPeriodEnd": DEC}, {"status": "OPEN",
                                                                  "periodEnd": DEC}),
    ({"status": "ACTIVE", "currentPeriodEnd": DEC}, {"status": ("CONFLICT", "PAID"),
                                                     "periodEnd": DEC}),
    ({"status": "ACTIVE", "currentPeriodEnd": "not a date"}, {"status": "OPEN",
                                                              "periodEnd": DEC}),
])
def test_an_unresolved_input_is_never_read_as_a_missing_invoice(subscription, invoice):
    assert engine.run(_snapshot(subscription, [invoice])) == []


def test_identical_snapshots_give_identical_findings():
    snapshot = _snapshot({"status": "ACTIVE", "currentPeriodEnd": DEC},
                         [{"status": "OPEN", "periodEnd": DEC}])
    assert engine.run(snapshot) == engine.run(snapshot)


# Database: the full pipeline over the fixture


@pytest.mark.db
def test_the_pack_runs_its_fixture_without_finops():
    member = saas_tenant()
    seed_saas(member)
    run = member.run()
    assert run["status"] == "SUCCEEDED"
    assert run["rule_version"] == "saas@0.1.0/saas.engine"
    cases = member.cases()
    found = rules(cases)
    assert len(cases) == 2
    assert sorted(found) == sorted(f["rule"] for f in manifest()["expected_findings"])

    renewed = found["RENEWED_WITHOUT_PAID_INVOICE"]
    assert [i["status"] for i in renewed["observed"]["invoices"]] == ["OPEN"]
    paid = found["PAID_WITHOUT_RENEWAL"]
    assert paid["observed"]["current_period_end"] == NOV
    assert [i["period_end"] for i in paid["observed"]["paid_invoices"]] == [
        "2027-11-01T00:00:00+00:00"
    ]
    assert member.entities("finops:Capture") == []


@pytest.mark.db
def test_a_second_run_over_the_same_inputs_opens_no_new_case():
    member = saas_tenant()
    seed_saas(member)
    first = member.run()
    second = member.run()
    assert second["inputs_hash"] == first["inputs_hash"]
    assert not any(f["new_case"] for f in second["findings"])
    assert len(member.cases()) == 2


@pytest.mark.db
def test_both_packs_run_side_by_side_in_one_tenant():
    member = saas_tenant(("finops", "saas"))
    seed_reconciliation(member)
    seed_saas(member)
    unnamed = member.request("POST", "/api/domain-runs", json=None)
    assert unnamed.status_code == 422  # two active packs: the run must name one

    finance = member.run(namespace="finops")
    saas = member.run(namespace="saas")
    assert finance["rule_version"].startswith("finops@")
    assert saas["rule_version"].startswith("saas@")
    assert {f["case_type"] for f in finance["findings"]} == {
        "DUPLICATE_CAPTURE", "SETTLEMENT_VARIANCE", "STATUS_MISMATCH"
    }
    assert {f["case_type"] for f in saas["findings"]} == {"RENEWAL_INVOICE_MISMATCH"}
    assert len(rules(member.cases())) == 2
    # Each pack projects only its own classes, so neither run reads the other's entities.
    assert len(member.entities("saas:Subscription")) == 4
    assert member.entities("finops:Capture")
