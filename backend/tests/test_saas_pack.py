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
from app.semantics.artifacts import core_artifact, pack_artifacts
from app.semantics.generation import (
    DiscoveredObject,
    OntologyProposal,
    ground_items,
    term_details,
)
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


# From generated proposals to cases, on the live demo business's rows

# The demo business's rows as its database holds them: lower-case statuses, zoned timestamps.
KITEBASE_SUBSCRIPTIONS = [
    {"subscription_id": "SUB-2-14", "account_id": "ACC-2-13", "plan_id": "pro-monthly",
     "status": "active", "current_period_end": "2026-12-01T00:00:00+00:00"},
    {"subscription_id": "SUB-2-18", "account_id": "ACC-2-17", "plan_id": "pro-monthly",
     "status": "active", "current_period_end": "2026-12-01T00:00:00+00:00"},
    {"subscription_id": "SUB-2-22", "account_id": "ACC-2-21", "plan_id": "team-annual",
     "status": "past_due", "current_period_end": "2026-11-01T00:00:00+00:00"},
]
KITEBASE_INVOICES = [
    {"invoice_id": invoice, "account_id": account, "subscription_id": subscription,
     "order_ref": None, "period_end": end, "status": status, "amount": amount, "currency": "USD"}
    for invoice, account, subscription, end, status, amount in [
        ("INV-2-15", "ACC-2-13", "SUB-2-14", "2026-11-01T00:00:00+00:00", "paid", "29.00"),
        ("INV-2-16", "ACC-2-13", "SUB-2-14", "2026-12-01T00:00:00+00:00", "paid", "29.00"),
        ("INV-2-19", "ACC-2-17", "SUB-2-18", "2026-11-01T00:00:00+00:00", "paid", "29.00"),
        ("INV-2-20", "ACC-2-17", "SUB-2-18", "2026-12-01T00:00:00+00:00", "open", "29.00"),
        ("INV-2-23", "ACC-2-21", "SUB-2-22", "2026-11-01T00:00:00+00:00", "paid", "290.00"),
        ("INV-2-24", "ACC-2-21", "SUB-2-22", "2027-11-01T00:00:00+00:00", "paid", "290.00"),
    ]
]


def _discovered(obj: str, columns: str) -> dict:
    fields = []
    for column in columns.split(","):
        name, datatype, nullable, unique = column.split(":")
        fields.append({"name": name, "datatype": datatype, "nullable": nullable == "true",
                       "unique": unique == "true"})
    return {"source_id": "billing", "object": obj, "fields": fields}


KITEBASE_METADATA = [
    _discovered("subscriptions", "subscription_id:string:false:true,account_id:string:false:false,"
                "plan_id:string:false:false,status:string:false:false,"
                "current_period_end:datetime:true:false"),
    _discovered("invoices", "invoice_id:string:false:true,account_id:string:false:false,"
                "subscription_id:string:true:false,order_ref:string:true:false,"
                "period_end:datetime:true:false,status:string:false:false,"
                "amount:decimal:false:false,currency:string:false:false"),
]


def _proposed(obj: str, field: str, prop: str, cls: str, kind: str,
              currency: str | None = None) -> dict:
    return {"source_id": "billing", "object": obj, "field": field, "property": prop,
            "class_iri": cls, "value_kind": kind, "currency_field": currency,
            "confidence": "high",
            "evidence": [{"kind": "field_profile", "field": field, "detail": "a column"}]}


# What the model answered for the live tenant: the subscription's status and period end went to
# new lookalike terms instead of the pack's, so the renewal check never saw a subscription.
LIVE_ANSWER = {
    "terms": [
        {"id": term_id, "kind": "property", "label": label, "subclass_of": None,
         "domain": "saas:Subscription", "range": None,
         "evidence": [{"kind": "field_profile", "field": field, "detail": "a column"}]}
        for term_id, label, field in [
            ("tenant:subscriptionStatus", "Subscription status", "status"),
            ("tenant:subscriptionCurrentPeriodEnd", "Subscription current period end",
             "current_period_end"),
        ]
    ],
    "bindings": [
        _proposed("subscriptions", "subscription_id", "saas:subscriptionReference",
                  "saas:Subscription", "identifier"),
        _proposed("subscriptions", "account_id", "saas:accountReference", "saas:Subscription",
                  "identifier"),
        _proposed("subscriptions", "plan_id", "saas:planReference", "saas:Subscription",
                  "identifier"),
        _proposed("subscriptions", "status", "tenant:subscriptionStatus", "saas:Subscription",
                  "status"),
        _proposed("subscriptions", "current_period_end", "tenant:subscriptionCurrentPeriodEnd",
                  "saas:Subscription", "timestamp"),
        _proposed("invoices", "invoice_id", "saas:invoiceReference", "saas:Invoice",
                  "identifier"),
        _proposed("invoices", "subscription_id", "saas:subscriptionReference", "saas:Invoice",
                  "identifier"),
        _proposed("invoices", "status", "saas:status", "saas:Invoice", "status"),
        _proposed("invoices", "period_end", "saas:periodEnd", "saas:Invoice", "timestamp"),
        _proposed("invoices", "amount", "saas:amount", "saas:Invoice", "money", "currency"),
        _proposed("invoices", "currency", "saas:currency", "saas:Invoice", "code"),
    ],
}
MAPPING_KEYS = ("datatype", "transform", "value_basis", "temporal_context", "currency_field")


def _accepted_bindings() -> list[tuple]:
    """The live answer grounded against the composed terms, then accepted as it stands."""
    packs = load_domain_packs()
    graph = [node for artifact in [core_artifact(), *pack_artifacts(packs["finops"]),
                                   *pack_artifacts(packs["saas"])]
             for node in artifact.content["@graph"]]
    details = term_details(graph)
    grounded, refused = ground_items(
        OntologyProposal.model_validate(LIVE_ANSWER),
        [DiscoveredObject(**item) for item in KITEBASE_METADATA],
        [term["id"] for term in details], details,
    )
    assert refused == []
    by_object: dict[tuple[str, str], list] = {}
    for item in grounded:
        if item["type"] == "binding":
            mapping = item["mapping"]
            by_object.setdefault((item["object"], mapping["class_iri"]), []).append(
                (item["field"], item["property"], {key: mapping[key] for key in MAPPING_KEYS}))
    return [("billing", obj, cls, fields) for (obj, cls), fields in by_object.items()]


def test_the_live_answer_binds_status_and_period_end_to_the_packs_terms():
    bound = {(obj, field): prop for _, obj, _, fields in _accepted_bindings()
             for field, prop, _ in fields}
    assert bound[("subscriptions", "status")] == "saas:status"
    assert bound[("subscriptions", "current_period_end")] == "saas:currentPeriodEnd"
    assert not any(prop.startswith("tenant:") for prop in bound.values())


@pytest.mark.db
def test_the_demo_business_discrepancies_are_found_from_generated_bindings():
    member = FinanceTenant(
        packs=("finops", "saas"), sources=("billing",), bindings=_accepted_bindings(),
        authority=SAAS_AUTHORITY, object_types=SAAS_OBJECTS,
    )
    for row in KITEBASE_SUBSCRIPTIONS:
        member.ingest("billing", "subscriptions", row, external_id=row["subscription_id"])
    for row in KITEBASE_INVOICES:
        member.ingest("billing", "invoices", row, external_id=row["invoice_id"])

    run = member.run(namespace="saas")
    assert run["status"] == "SUCCEEDED"
    found = rules(member.cases())
    assert sorted(found) == ["PAID_WITHOUT_RENEWAL", "RENEWED_WITHOUT_PAID_INVOICE"]
    # SUB-2-18 renewed to December with December's invoice still open.
    renewed = found["RENEWED_WITHOUT_PAID_INVOICE"]
    assert renewed["observed"]["subscription_status"] == "ACTIVE"
    assert sorted((i["status"], i["period_end"]) for i in renewed["observed"]["invoices"]) == [
        ("OPEN", DEC), ("PAID", NOV)]
    # SUB-2-22 paid through November 2027 but still ends in November 2026.
    paid = found["PAID_WITHOUT_RENEWAL"]
    assert paid["observed"]["current_period_end"] == NOV
    assert [i["period_end"] for i in paid["observed"]["paid_invoices"]] == [
        "2027-11-01T00:00:00+00:00"]
    # SUB-2-14 is paid for its period, so it raises nothing.
    assert len(found) == len(member.cases()) == 2
