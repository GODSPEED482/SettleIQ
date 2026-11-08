"""Connector, domain-pack, envelope and agent-tool contracts."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.authz.catalogue import get_catalogue
from app.connectors.manifest import (
    ConnectorManifest,
    load_capability_registry,
    load_connector_manifests,
    validate_manifest,
)
from app.domain_packs.manifest import AgentTool, DomainPackManifest, load_domain_packs
from app.investigation.tools import ToolRegistryError, build_tool_registry, validate_tools
from app.sources.envelope import SourceRecordEnvelope
from packs.finops.money import Money, MoneyError


def keys_of(manifest: ConnectorManifest) -> set[str]:
    return {capability.key for capability in manifest.capabilities}


def test_paypal_publishes_only_canonical_keys():
    assert keys_of(load_connector_manifests()["paypal"]) == {
        "transactions.read", "captures.read", "refunds.read", "refunds.create",
        "webhooks.receive", "reports.balance_reconciliation.read", "files.upload",
    }


def test_internal_merchant_publishes_only_its_two_keys():
    assert keys_of(load_connector_manifests()["internal-merchant"]) == {
        "merchant.payment.read", "merchant.payment_status.correct",
    }


def test_merchant_correction_schema_only_allows_pending_to_completed():
    manifest = load_connector_manifests()["internal-merchant"]
    action = next(c for c in manifest.capabilities if c.key == "merchant.payment_status.correct")
    assert action.parameters["additionalProperties"] is False
    assert action.parameters["properties"]["expected_current_status"] == {"const": "PENDING"}
    assert action.parameters["properties"]["requested_status"] == {"const": "COMPLETED"}


def test_legacy_capability_spelling_is_rejected():
    paypal = load_connector_manifests()["paypal"]
    data = paypal.model_dump()
    data["capabilities"].append({"key": "batch_read", "version": "1.0.0", "parameters": {}})
    with pytest.raises(ValueError, match="non-canonical"):
        validate_manifest(ConnectorManifest(**data), load_capability_registry())


def test_manifest_flags_cannot_contradict_capabilities():
    paypal = load_connector_manifests()["paypal"]
    data = paypal.model_dump() | {"supports_events": False}
    with pytest.raises(ValueError, match="supports_events"):
        validate_manifest(ConnectorManifest(**data), load_capability_registry())


def test_unsupported_capability_must_be_stated():
    data = load_connector_manifests()["paypal"].model_dump()
    del data["supports_incremental"]
    with pytest.raises(ValidationError):
        ConnectorManifest(**data)


def pack_data(**overrides) -> dict:
    return load_domain_packs()["saas"].model_dump() | overrides


def test_shipped_packs_load():
    assert set(load_domain_packs()) == {"finops", "saas"}


@pytest.mark.parametrize("namespace", ["core", "platform"])
def test_reserved_pack_namespace_is_rejected(namespace):
    with pytest.raises(ValidationError, match="reserved namespace"):
        DomainPackManifest(**pack_data(namespace=namespace))


def test_pack_permissions_must_be_namespaced():
    with pytest.raises(ValidationError, match="outside namespace"):
        DomainPackManifest(**pack_data(permissions=["finops.read"]))


def test_pack_must_reference_canonical_action_capabilities():
    with pytest.raises(ValidationError, match="canonical action"):
        DomainPackManifest(**pack_data(connector_action_capabilities=["refund_money"]))
    with pytest.raises(ValidationError, match="canonical action"):
        DomainPackManifest(**pack_data(connector_action_capabilities=["captures.read"]))


def test_pb_003_is_not_executable():
    templates = {t.id: t for t in load_domain_packs()["finops"].playbook_templates}
    assert templates["PB-003"].executable is False


def test_registry_has_the_eleven_spec_tools():
    names = {t.name for t in build_tool_registry(load_domain_packs(), get_catalogue())}
    assert len(names) == 11
    assert "calculate_settlement_variance" in names


@pytest.mark.parametrize(
    "name", ["request_approval", "create_playbook_request", "approve", "reject", "execute",
             "approve_refund"],
)
def test_prohibited_agent_tools_fail_validation(name):
    with pytest.raises(ToolRegistryError, match="prohibited"):
        validate_tools([AgentTool(name=name, permission="case.read")], get_catalogue())


def test_agent_tool_needs_an_agent_permitted_key():
    with pytest.raises(ToolRegistryError, match="not agent-permitted"):
        validate_tools([AgentTool(name="peek", permission="audit.read")], get_catalogue())


def envelope(**overrides) -> SourceRecordEnvelope:
    data = {
        "id": uuid4(), "tenant_id": uuid4(), "source_id": uuid4(), "source_object_id": uuid4(),
        "external_id": None, "record_type": "crm:Ticket",
        "attributes": {"anything": {"nested": [1, 2]}}, "raw_evidence_id": uuid4(),
        "occurred_at": None, "observed_at": datetime.now(UTC), "payload_hash": "a" * 64,
        "connector_version": "1.0.0", "mapping_version": None,
    }
    return SourceRecordEnvelope(**(data | overrides))


def test_envelope_preserves_non_financial_attributes():
    assert envelope().attributes == {"anything": {"nested": [1, 2]}}


def test_envelope_has_no_financial_fields():
    assert not set(SourceRecordEnvelope.model_fields) & {"amount", "currency", "status", "fee"}


@pytest.mark.parametrize(
    "bad", [{"payload_hash": "xyz"}, {"record_type": "untyped"}, {"amount": "1.00"},
            {"observed_at": datetime(2026, 1, 1)}],
)
def test_envelope_rejects_invalid_records(bad):
    with pytest.raises(ValidationError):
        envelope(**bad)


def usd(amount: str) -> Money:
    return Money.parse(amount, "USD")


def test_money_arithmetic_is_exact():
    assert usd("0.1") + usd("0.2") == usd("0.3")
    assert usd("100.000001") - usd("0.000001") == usd("100")


@pytest.mark.parametrize("amount", [100.0, 100])
def test_money_rejects_non_decimal_amounts(amount):
    with pytest.raises(MoneyError, match="Decimal"):
        Money(amount, "USD")


@pytest.mark.parametrize("amount", ["1.0000001", "NaN", "Infinity", "100000000000000", "abc"])
def test_money_rejects_values_outside_numeric_20_6(amount):
    with pytest.raises(MoneyError):
        Money.parse(amount, "USD")


def test_money_refuses_cross_currency_arithmetic():
    with pytest.raises(MoneyError, match="cannot combine"):
        usd("1") + Money.parse("1", "EUR")


def test_money_rejects_invalid_currency():
    with pytest.raises(MoneyError, match="invalid currency"):
        Money(Decimal("1"), "usd")


def test_shacl_toolchain_reports_a_violation():
    from pyshacl import validate
    from rdflib import Graph

    shapes = Graph().parse(
        format="turtle",
        data="""
        @prefix sh: <http://www.w3.org/ns/shacl#> .
        @prefix ex: <http://example.org/> .
        ex:S a sh:NodeShape ; sh:targetClass ex:Payment ;
            sh:property [ sh:path ex:amount ; sh:minCount 1 ] .
        """,
    )
    data = Graph().parse(
        format="json-ld",
        data='{"@id": "http://example.org/p1", "@type": "http://example.org/Payment"}',
    )
    conforms, _, _ = validate(data, shacl_graph=shapes)
    assert conforms is False
