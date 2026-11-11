"""Playbook definitions as validated pack data, and PB-001's preconditions (task 5.9, spec 9.1,
9.2, 4.4.1).

A definition that names an unregistered case type, pack code outside its pack, an action other
than its template's, or a readback that is not a declared dependency fails startup. PB-001 is
eligible only when PayPal authoritatively says COMPLETED, the merchant still says PENDING, and
amount, currency and order agree; its target is one conditional PENDING -> COMPLETED update
(AC-PLY-01).
"""

import shutil
from pathlib import Path

import pytest
import yaml

from app.domain_packs.manifest import PACKS_DIR, load_domain_packs
from app.domain_packs.playbooks import PlaybookDefinitionError, get_playbooks, load_playbooks
from app.domain_packs.registry import PackRegistryError
from packs.finops.playbooks import payment_state_recovery

PB001 = "playbook_definitions/pb-001.yaml"


def test_pb001_is_loaded_with_its_definition():
    playbook = get_playbooks()["finops:PB-001"]
    definition = playbook.definition
    assert definition.trigger_case_types == ["STATUS_MISMATCH"]
    assert definition.approval_policy == "TENANT_POLICY"
    assert (definition.action.connector_id, definition.action.capability) == (
        "internal-merchant", "merchant.payment_status.correct"
    )
    assert definition.verification.readbacks == ["merchant.payment.read"]
    assert definition.failure_path.blind_retry is False


def test_a_non_executable_template_has_no_playbook():
    assert "finops:PB-003" not in get_playbooks()


@pytest.fixture
def pack_copy(tmp_path: Path) -> Path:
    """A writable copy of the finops pack, loaded from its own directory."""
    shutil.copytree(PACKS_DIR / "finops", tmp_path / "finops")
    return tmp_path


def _edit(directory: Path, **changes) -> None:
    path = directory / "finops" / PB001
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data.update(changes)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


def _load(directory: Path):
    return load_playbooks(load_domain_packs(directory), directory)


@pytest.mark.parametrize(("changes", "message"), [
    ({"trigger_case_types": ["NOT_A_CASE"]}, "unregistered case types"),
    ({"preconditions": "packs.other.code:check"}, "outside the pack"),
    ({"action": {"connector_id": "internal-merchant", "capability": "merchant.payment.read"}},
     "template's capability"),
    ({"action": {"connector_id": "paypal", "capability": "merchant.payment_status.correct"}},
     "does not declare"),
    ({"verification": {"readbacks": ["captures.read"], "condition": "x"}}, "not a dependency"),
    ({"id": "PB-009"}, "definition is for"),
    ({"fixtures": ["fixtures/missing.json"]}, "missing fixtures"),
])
def test_an_invalid_definition_fails_loading(pack_copy, changes, message):
    _edit(pack_copy, **changes)
    with pytest.raises(PlaybookDefinitionError, match=message):
        _load(pack_copy)


def test_unknown_fields_are_refused(pack_copy):
    _edit(pack_copy, approve_automatically=True)
    with pytest.raises(ValueError, match="approve_automatically"):
        _load(pack_copy)


def test_blind_retry_is_refused(pack_copy):
    _edit(pack_copy, failure_path={"on_failure": "HUMAN_REVIEW",
                                   "on_ambiguous_outcome": "MANUAL_REVIEW", "blind_retry": True})
    with pytest.raises(ValueError, match="blind_retry"):
        _load(pack_copy)


def test_unresolvable_preconditions_fail_loading(pack_copy):
    _edit(pack_copy, preconditions="packs.finops.playbooks:no_such_check")
    with pytest.raises(PackRegistryError):
        _load(pack_copy)


def test_a_missing_definition_file_fails_loading(pack_copy):
    (pack_copy / "finops" / PB001).unlink()
    with pytest.raises(ValueError, match="missing files"):
        load_domain_packs(pack_copy)


# PB-001 preconditions over a case snapshot

PAYPAL, MERCHANT = "s-paypal", "s-merchant"
ENTITY = "e-capture"


def _obs(i: str, source: str, prop: str, value: str, numeric: str | None = None,
         currency: str | None = None, at: str = "2026-03-01T00:00:01+00:00") -> dict:
    return {"id": i, "entity_id": ENTITY, "property_iri": f"finops:{prop}", "source_id": source,
            "source_record_id": f"r-{i}", "external_id": f"x-{source}", "value": value,
            "numeric": numeric, "currency": currency, "observed_at": at}


def snapshot(**overrides) -> dict:
    observations = {
        "pp-status": _obs("pp-status", PAYPAL, "lifecycleStatus", "COMPLETED"),
        "m-status": _obs("m-status", MERCHANT, "lifecycleStatus", "PENDING"),
        "pp-amount": _obs("pp-amount", PAYPAL, "amount", "100.00", "100.000000", "USD"),
        "m-amount": _obs("m-amount", MERCHANT, "amount", "100.00", "100.000000", "USD"),
        "pp-order": _obs("pp-order", PAYPAL, "orderReference", "ORD-1"),
        "m-order": _obs("m-order", MERCHANT, "orderReference", "ORD-1"),
    }
    view = {"entity_id": ENTITY, "property_iri": "finops:lifecycleStatus", "status": "RESOLVED",
            "value": "COMPLETED", "authority_source_id": PAYPAL}
    view.update(overrides.pop("view", {}))
    for key, value in overrides.pop("observations", {}).items():
        if value is None:
            observations.pop(key)
        else:
            observations[key] = {**observations[key], **value}
    case = {"case_type": "STATUS_MISMATCH", "property_iris": ["finops:lifecycleStatus"],
            "entity_ids": [ENTITY]}
    case.update(overrides.pop("case", {}))
    return {
        "case": case,
        "sources": {PAYPAL: "paypal", MERCHANT: "merchant-postgres"},
        "entities": [{"id": ENTITY, "class_iri": "finops:Capture"}],
        "observations": list(observations.values()),
        "views": [view],
        "relationships": [],
    }


def test_pb001_is_eligible_with_the_one_conditional_transition():
    result = payment_state_recovery(snapshot(), {})
    assert result["eligible"] is True, result["failures"]
    assert result["target"] == {
        "merchant_source_id": MERCHANT, "payment_id": "x-s-merchant",
        "expected_current_status": "PENDING", "requested_status": "COMPLETED",
    }
    assert result["proof"]["amount"] == "100.000000" and result["proof"]["currency"] == "USD"
    assert {"pp-status", "m-status", "r-pp-status"} <= set(result["evidence_ids"])


@pytest.mark.parametrize(("overrides", "failure"), [
    ({"case": {"case_type": "AMOUNT_MISMATCH"}}, "not a lifecycle status mismatch"),
    ({"case": {"entity_ids": [ENTITY, "e-other"]}}, "exactly one payment"),
    ({"view": {"status": "CONFLICT"}}, "not authoritatively COMPLETED"),
    ({"view": {"authority_source_id": MERCHANT}}, "does not come from PayPal"),
    ({"observations": {"m-status": {"value": "COMPLETED"}}}, "not PENDING"),
    ({"observations": {"pp-status": None}}, "no PayPal observation"),
    ({"observations": {"m-amount": {"numeric": "99.000000"}}}, "disagree"),
    ({"observations": {"m-amount": {"currency": "EUR"}}}, "disagree"),
    ({"observations": {"pp-amount": None}}, "not observed by both"),
    ({"observations": {"m-order": {"value": "ORD-2"}}}, "order relationship"),
])
def test_pb001_is_ineligible_when_any_precondition_fails(overrides, failure):
    result = payment_state_recovery(snapshot(**overrides), {})
    assert result["eligible"] is False and result["target"] is None
    assert any(failure in f for f in result["failures"]), result["failures"]


def test_pb001_reads_the_latest_merchant_status():
    later = {"m-status": {"value": "COMPLETED", "observed_at": "2026-03-02T00:00:00+00:00"}}
    early = snapshot()["observations"][1] | {"id": "m-old"}
    data = snapshot(observations=later)
    data["observations"].append(early)
    assert payment_state_recovery(data, {})["eligible"] is False
