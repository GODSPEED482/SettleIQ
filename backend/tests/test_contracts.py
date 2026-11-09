"""Connector, domain-pack, envelope and agent-tool contracts."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
import yaml
from pydantic import ValidationError

from app.authz.catalogue import get_catalogue
from app.connectors.manifest import (
    ConnectorManifest,
    load_capability_registry,
    load_connector_manifests,
    validate_manifest,
)
from app.domain_packs.manifest import AgentTool, DomainPackManifest, load_domain_packs
from app.domain_packs.registry import (
    PackActivationError,
    PackRegistryError,
    assert_can_activate,
    resolve_entrypoint,
    resolve_pack_entrypoints,
)
from app.investigation.tools import ToolRegistryError, build_tool_registry, validate_tools
from app.sources.envelope import SourceRecordEnvelope
from packs.finops.money import Money, MoneyError


# TODO(G1 1.6 Rahul): re-check these tests against the frozen connector manifest and envelope.
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


def test_shipped_pack_package_can_differ_from_namespace():
    packs = load_domain_packs()
    assert packs["saas"].package == "saas_subscription"
    assert packs["finops"].package == "finops"


def entrypoint(target: str) -> list[dict]:
    return [{"id": "rule", "entrypoint": target}]


@pytest.mark.parametrize(
    "target",
    [
        "packs.finops.validators:check",
        "packs.saas_subscription_evil.validators:check",
        "os:system",
    ],
)
def test_entrypoint_outside_the_packs_own_package_is_refused(target):
    with pytest.raises(ValidationError, match="outside package"):
        DomainPackManifest(**pack_data(validators=entrypoint(target)))


@pytest.mark.parametrize("target", ["packs.saas_subscription..x:f", "no_function", "a b:c"])
def test_malformed_entrypoint_is_refused(target):
    with pytest.raises(ValidationError, match="pattern"):
        DomainPackManifest(**pack_data(validators=entrypoint(target)))


def test_domain_engine_is_confined_to_its_package():
    engine = {"id": "engine", "entrypoint": "packs.finops.engine:run"}
    with pytest.raises(ValidationError, match="outside package"):
        DomainPackManifest(**pack_data(domain_engine=engine))


@pytest.mark.parametrize("path", ["../finops/secret.jsonld", "/etc/passwd"])
def test_pack_file_cannot_leave_its_directory(path):
    with pytest.raises(ValidationError, match="pattern"):
        DomainPackManifest(**pack_data(ontology_definitions=[{"id": "x", "path": path}]))


def test_invalid_version_range_is_refused():
    with pytest.raises(ValidationError, match="invalid version range"):
        DomainPackManifest(**pack_data(compatible_platform_versions="not a range"))
    with pytest.raises(ValidationError, match="invalid version range"):
        DomainPackManifest(**pack_data(dependencies=[{"namespace": "b", "version": "nope"}]))


def template(**overrides) -> dict:
    return {"id": "T", "title": "T", "executable": False} | overrides


def test_playbook_dependency_must_be_a_canonical_read():
    finops = load_domain_packs()["finops"].model_dump()
    bad = template(capability_dependencies=["refunds.create"])
    with pytest.raises(ValidationError, match="canonical read"):
        DomainPackManifest(**finops | {"playbook_templates": [bad]})


def test_certified_playbooks_declare_their_readbacks():
    templates = {t.id: t for t in load_domain_packs()["finops"].playbook_templates}
    assert templates["PB-001"].capability_dependencies == ["merchant.payment.read"]
    assert set(templates["PB-002"].capability_dependencies) == {"refunds.read", "captures.read"}


def write_pack(root, directory: str, **overrides) -> None:
    (root / directory).mkdir()
    data = pack_data(package=directory) | overrides
    (root / directory / "manifest.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


def test_package_must_match_its_directory(tmp_path):
    write_pack(tmp_path, "renamed", package="saas_subscription")
    with pytest.raises(ValueError, match="does not match"):
        load_domain_packs(tmp_path)


def test_declared_pack_file_must_exist(tmp_path):
    write_pack(tmp_path, "pack", shacl_shapes=[{"id": "s", "path": "shapes.ttl"}])
    with pytest.raises(ValueError, match="missing files"):
        load_domain_packs(tmp_path)


def test_pack_incompatible_with_the_platform_is_not_loaded(tmp_path):
    write_pack(tmp_path, "pack", compatible_platform_versions=">=9.0.0")
    with pytest.raises(ValueError, match="incompatible"):
        load_domain_packs(tmp_path)


def test_entrypoint_resolves_to_pack_code():
    assert resolve_entrypoint("packs.finops.money:Money") is Money


@pytest.mark.parametrize("target", ["packs.finops.nothing:f", "packs.finops.money:missing"])
def test_unresolvable_entrypoint_fails(target):
    with pytest.raises(PackRegistryError, match="cannot resolve"):
        resolve_entrypoint(target)


def test_non_callable_entrypoint_fails():
    with pytest.raises(PackRegistryError, match="not callable"):
        resolve_entrypoint("packs.finops.money:_SCALE")


def test_startup_check_fails_on_missing_pack_code():
    broken = DomainPackManifest(
        **pack_data(validators=entrypoint("packs.saas_subscription.nothing:check"))
    )
    with pytest.raises(PackRegistryError):
        resolve_pack_entrypoints({"saas": broken})


def pack(namespace: str, **overrides) -> DomainPackManifest:
    return DomainPackManifest(**pack_data(namespace=namespace, **overrides))


def test_compatible_packs_can_be_active_together():
    assert_can_activate(pack("a"), [pack("b"), pack("c")])


def test_reactivating_a_namespace_is_not_a_conflict_with_itself():
    assert_can_activate(pack("a", conflicts=["a"]), [pack("a")])


def test_conflict_declared_by_the_candidate_refuses():
    with pytest.raises(PackActivationError, match="conflicts"):
        assert_can_activate(pack("a", conflicts=["b"]), [pack("b")])


def test_conflict_declared_by_an_active_pack_refuses():
    with pytest.raises(PackActivationError, match="conflicts"):
        assert_can_activate(pack("a"), [pack("b", conflicts=["a"])])


def needs_b(version: str) -> DomainPackManifest:
    return pack("a", dependencies=[{"namespace": "b", "version": version}])


def test_missing_dependency_refuses():
    with pytest.raises(PackActivationError, match="needs b"):
        assert_can_activate(needs_b(">=0.1.0"), [])


def test_dependency_outside_its_range_refuses():
    with pytest.raises(PackActivationError, match="needs b"):
        assert_can_activate(needs_b(">=2.0.0"), [pack("b")])


def test_dependency_in_range_is_accepted():
    assert_can_activate(needs_b(">=0.1.0,<1.0.0"), [pack("b")])


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
