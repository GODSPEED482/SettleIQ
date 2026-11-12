"""PB-002 definition, duplicate proof and recommendation (task 5.13, spec 9.2, 10.6).

PB-002 is eligible only with two distinct PayPal captures, both authoritatively COMPLETED, for
one order the merchant knows, with PayPal's capture times telling the original from the
duplicate, equal amount and currency, and no refund or dispute already touching the order. Equal
amounts alone never qualify (AC-PLY-02). Approval is always required, whatever the tenant's
policy says (AC-PLY-08, PB-002 part).
"""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from app.db import scoped_tx
from app.domain_packs.playbooks import get_playbooks
from app.playbooks.policy import approval_policy, evaluate, recommend
from packs.finops.playbooks import duplicate_capture_refund
from tests import domain_fixture
from tests.domain_fixture import IDENTIFIER, STATUS, FinanceTenant, money

ORDER, CAP_A, CAP_B = "e-order", "e-cap-a", "e-cap-b"
PAYPAL, MERCHANT = "s-paypal", "s-merchant"


def _obs(i: str, entity: str, prop: str, value: str, source: str = PAYPAL,
         numeric: str | None = None, currency: str | None = None) -> dict:
    return {"id": i, "entity_id": entity, "property_iri": f"finops:{prop}", "source_id": source,
            "source_record_id": f"r-{i}", "external_id": f"x-{i}", "value": value,
            "numeric": numeric, "currency": currency, "observed_at": "2026-03-01T00:00:01+00:00"}


def snapshot(drop: tuple[str, ...] = (), extra_entities: list | None = None,
             extra_relationships: list | None = None, **changes) -> dict:
    observations = {
        "ref-a": _obs("ref-a", CAP_A, "providerReference", "CAP-A"),
        "ref-b": _obs("ref-b", CAP_B, "providerReference", "CAP-B"),
        "amt-a": _obs("amt-a", CAP_A, "amount", "50.00", numeric="50.000000", currency="USD"),
        "amt-b": _obs("amt-b", CAP_B, "amount", "50.00", numeric="50.000000", currency="USD"),
        "at-a": _obs("at-a", CAP_A, "occurredAt", "2026-03-01T10:00:00+00:00"),
        "at-b": _obs("at-b", CAP_B, "occurredAt", "2026-03-01T10:05:00+00:00"),
        "order": _obs("order", ORDER, "orderReference", "ORD-2", source=MERCHANT),
    }
    for key, value in changes.items():
        observations[key] = {**observations[key], **value}
    for key in drop:
        observations.pop(key)
    statuses = [{"entity_id": c, "property_iri": "finops:lifecycleStatus", "status": "RESOLVED",
                 "value": "COMPLETED", "authority_source_id": PAYPAL} for c in (CAP_A, CAP_B)]
    return {
        "case": {"case_type": "DUPLICATE_CAPTURE", "entity_ids": [ORDER, CAP_A, CAP_B]},
        "sources": {PAYPAL: "paypal", MERCHANT: "merchant-postgres"},
        "entities": [{"id": ORDER, "class_iri": "finops:Order"},
                     {"id": CAP_A, "class_iri": "finops:Capture"},
                     {"id": CAP_B, "class_iri": "finops:Capture"}, *(extra_entities or [])],
        "observations": list(observations.values()),
        "views": statuses,
        "relationships": [
            {"id": "rel-a", "subject": CAP_A, "predicate": "finops:forOrder", "object": ORDER},
            {"id": "rel-b", "subject": CAP_B, "predicate": "finops:forOrder", "object": ORDER},
            *(extra_relationships or []),
        ],
    }


def test_pb002_refunds_the_later_capture_in_full():
    result = duplicate_capture_refund(snapshot(), {})
    assert result["eligible"] is True, result["failures"]
    assert result["target"] == {"paypal_source_id": PAYPAL, "capture_id": "CAP-B",
                                "amount": "50.00", "currency": "USD"}
    assert result["proof"]["original"]["capture_id"] == "CAP-A"
    assert result["proof"]["duplicate"]["capture_id"] == "CAP-B"
    assert result["proof"]["order_reference"] == "ORD-2"


def test_the_original_is_decided_by_paypals_capture_time_not_by_order_of_ids():
    later_a = {"at-a": {"value": "2026-03-01T11:00:00+00:00"}}
    assert duplicate_capture_refund(snapshot(**later_a), {})["target"]["capture_id"] == "CAP-A"


REFUND = {"id": "e-refund", "class_iri": "finops:Refund"}
CHARGEBACK = {"id": "e-cb", "class_iri": "finops:Chargeback"}


@pytest.mark.parametrize(("kwargs", "failure"), [
    ({"changes": {"ref-b": {"value": "CAP-A"}}}, "share a reference"),
    ({"changes": {"at-b": {"value": "2026-03-01T10:00:00+00:00"}}}, "cannot tell the original"),
    ({"changes": {"amt-b": {"numeric": "49.000000"}}}, "differ in amount or currency"),
    ({"changes": {"amt-b": {"currency": "EUR"}}}, "differ in amount or currency"),
    ({"drop": ("order",)}, "merchant does not know"),
    ({"drop": ("at-a",)}, "lacks PayPal's reference, amount or time"),
    ({"extra_entities": [REFUND], "extra_relationships": [
        {"id": "rel-r", "subject": "e-refund", "predicate": "finops:forOrder", "object": ORDER}]},
     "refund already exists"),
    ({"extra_relationships": [
        {"id": "rel-rc", "subject": "e-x", "predicate": "finops:refundsCapture",
         "object": CAP_B}]}, "refund already exists"),
    ({"extra_entities": [CHARGEBACK]}, "dispute or chargeback"),
])
def test_pb002_is_ineligible_without_the_full_proof(kwargs, failure):
    changes = kwargs.pop("changes", {})
    result = duplicate_capture_refund(snapshot(**kwargs, **changes), {})
    assert result["eligible"] is False and result["target"] is None
    assert any(failure in f for f in result["failures"]), result["failures"]


def test_a_capture_paypal_has_not_completed_blocks_the_refund():
    data = snapshot()
    data["views"][1]["value"] = "PENDING"
    result = duplicate_capture_refund(data, {})
    assert any("not authoritatively COMPLETED" in f for f in result["failures"])


def test_completion_from_a_non_paypal_authority_is_not_enough():
    data = snapshot()
    data["views"][0]["authority_source_id"] = MERCHANT
    assert duplicate_capture_refund(data, {})["eligible"] is False


def test_equal_amounts_on_two_orders_are_not_a_duplicate():
    """AC-PLY-02: same amount, different orders, so each order has one capture."""
    data = snapshot()
    data["relationships"][1]["object"] = "e-other-order"
    result = duplicate_capture_refund(data, {})
    assert result["eligible"] is False
    assert any("not linked to the order" in f for f in result["failures"])


def test_three_captures_are_ambiguous():
    data = snapshot()
    data["case"]["entity_ids"].append("e-cap-c")
    data["entities"].append({"id": "e-cap-c", "class_iri": "finops:Capture"})
    assert any("exactly two captures" in f for f in duplicate_capture_refund(data, {})["failures"])


def test_pb002_definition_always_requires_approval():
    definition = get_playbooks()["finops:PB-002"].definition
    assert definition.approval_policy == "ALWAYS_REQUIRED"
    assert (definition.action.connector_id, definition.action.capability) == (
        "paypal", "refunds.create")
    assert definition.verification.readbacks == ["refunds.read", "captures.read"]


# Against real data, through the recommendation service

CAPTURE_TIME = ("$.create_time", "finops:occurredAt", {
    "datatype": "datetime", "value_basis": "timestamp", "currency_field": None,
    "temporal_context": "at_event", "transform": {"name": "iso8601"}})
REFUNDS = ("paypal", "refunds", "finops:Refund", [
    ("$.id", "finops:providerReference", IDENTIFIER),
    ("$.invoice_id", "finops:orderReference", IDENTIFIER),
    ("$.status", "finops:lifecycleStatus", STATUS | {"transform": {"name": "upper"}}),
    ("$.amount.value", "finops:amount", money("$.amount.currency_code")),
])
CONNECTORS = {"paypal": "paypal", "merchant": "merchant-postgres"}


class PayPalFinanceTenant(FinanceTenant):
    """The G3 Finance tenant, with PayPal and the merchant registered under their real
    connectors, PayPal's capture time bound, and PayPal refunds ingested as Refunds."""

    def __init__(self) -> None:
        domain_fixture.OBJECT_TYPES.setdefault("refunds", "API")
        super().__init__(extra_bindings=[
            ("paypal", "captures", "finops:Capture", [CAPTURE_TIME]), REFUNDS])

    def _register_sources(self) -> None:
        with scoped_tx(self.tenant) as conn:
            for name, source_id in self.sources.items():
                conn.execute(
                    text(
                        """
                        INSERT INTO data_sources (id, tenant_id, connector_id,
                            connector_version, source_type, protocol, display_name, created_by)
                        VALUES (:id, :tenant, :connector, '1.0.0', 'test', 'http', :name, :user)
                        """
                    ),
                    {"id": source_id, "tenant": self.tenant,
                     "connector": CONNECTORS.get(name, f"test-{name}"), "name": name,
                     "user": self.principal.id},
                )

    def capture(self, capture_id: str, order: str, at: str, amount: str = "50.00") -> None:
        self.ingest("paypal", "captures", {
            "id": capture_id, "invoice_id": order, "status": "COMPLETED",
            "amount": {"value": amount, "currency_code": "USD"}, "create_time": at})

    def duplicate_case(self, order: str) -> dict:
        for case in self.cases(case_type="DUPLICATE_CAPTURE"):
            with scoped_tx(self.tenant) as conn:
                refs = conn.execute(
                    text(
                        "SELECT normalized_value FROM property_observations "
                        "WHERE entity_id = ANY (CAST(:ids AS uuid[])) "
                        "AND property_iri = 'finops:orderReference'"
                    ),
                    {"ids": case["entity_ids"]},
                ).scalars().all()
            if order in refs:
                return case
        raise AssertionError(f"no duplicate case for {order}")


@pytest.fixture(scope="module")
def paypal() -> PayPalFinanceTenant:
    return PayPalFinanceTenant()


def _recommend(tenant: PayPalFinanceTenant, case: dict) -> list[dict]:
    with scoped_tx(tenant.tenant) as conn:
        return recommend(conn, UUID(case["id"]))


@pytest.mark.db
def test_a_real_duplicate_is_recommended_for_pb002(paypal):
    paypal.ingest("merchant", "orders", {"order_id": "ORD-D1"})
    paypal.capture("CAP-D1", "ORD-D1", "2026-03-01T10:00:00Z")
    paypal.capture("CAP-D2", "ORD-D1", "2026-03-01T10:05:00Z")
    paypal.run()
    [item] = _recommend(paypal, paypal.duplicate_case("ORD-D1"))
    assert item["playbook"] == "finops:PB-002" and item["eligible"] is True, item["failures"]
    assert item["requires_approval"] is True
    assert item["target"]["capture_id"] == "CAP-D2"


@pytest.mark.db
def test_a_prior_refund_on_the_order_makes_pb002_ineligible(paypal):
    paypal.ingest("merchant", "orders", {"order_id": "ORD-D2"})
    paypal.capture("CAP-D3", "ORD-D2", "2026-03-01T10:00:00Z")
    paypal.capture("CAP-D4", "ORD-D2", "2026-03-01T10:05:00Z")
    paypal.ingest("paypal", "refunds", {"id": "REF-D4", "invoice_id": "ORD-D2",
                                        "status": "COMPLETED",
                                        "amount": {"value": "50.00", "currency_code": "USD"}})
    paypal.run()
    [item] = _recommend(paypal, paypal.duplicate_case("ORD-D2"))
    assert item["eligible"] is False
    assert any("refund already exists" in f for f in item["failures"])


@pytest.mark.db
def test_pb002_needs_approval_even_when_the_tenant_says_otherwise(paypal):
    with scoped_tx(paypal.tenant) as conn:
        conn.execute(
            text(
                """
                INSERT INTO playbook_approval_policies (id, tenant_id, pack_namespace,
                    playbook_id, version, approval_policy, reason, set_by)
                VALUES (:id, :t, 'finops', 'PB-002', 1, 'NOT_REQUIRED', 'test', :u)
                """
            ),
            {"id": uuid4(), "t": paypal.tenant, "u": paypal.principal.id},
        )
        policy = approval_policy(conn, get_playbooks()["finops:PB-002"])
    assert policy.required is True and policy.source == "definition"


@pytest.mark.db
def test_pb001_follows_the_tenants_latest_policy(paypal):
    playbook = get_playbooks()["finops:PB-001"]
    with scoped_tx(paypal.tenant) as conn:
        assert approval_policy(conn, playbook).version == "default:REQUIRED"
        conn.execute(
            text(
                """
                INSERT INTO playbook_approval_policies (id, tenant_id, pack_namespace,
                    playbook_id, version, approval_policy, reason, set_by)
                VALUES (:id, :t, 'finops', 'PB-001', 1, 'NOT_REQUIRED', 'low risk', :u)
                """
            ),
            {"id": uuid4(), "t": paypal.tenant, "u": paypal.principal.id},
        )
        policy = approval_policy(conn, playbook)
    assert (policy.required, policy.version) == (False, "tenant:1")


@pytest.mark.db
def test_evaluate_refuses_a_playbook_the_case_does_not_trigger(paypal):
    paypal.ingest("merchant", "orders", {"order_id": "ORD-D3"})
    paypal.capture("CAP-D5", "ORD-D3", "2026-03-01T10:00:00Z")
    paypal.capture("CAP-D6", "ORD-D3", "2026-03-01T10:05:00Z")
    paypal.run()
    case = paypal.duplicate_case("ORD-D3")
    with scoped_tx(paypal.tenant) as conn:
        result = evaluate(conn, UUID(case["id"]), get_playbooks()["finops:PB-001"])
    assert result["eligible"] is False
    assert "does not trigger" in result["failures"][0]
