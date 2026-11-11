"""Agent service boundary (spec 8): the private tool API, output contract and Compose isolation."""

import json
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.agent_tools_api import TOOL_PREFIX, create_agent_tools_app
from app.authz.catalogue import get_catalogue
from app.authz.guard import TENANT_HEADER, build_route_map
from app.domain_packs.manifest import load_domain_packs
from app.investigation.response import (
    AgentResponse,
    Expectations,
    ResponseRefused,
    validate_response,
)
from app.investigation.tools import build_tool_registry
from app.main import create_app
from app.worker_api import create_worker_app

REPO = Path(__file__).parents[2]
SERVICES = yaml.safe_load((REPO / "docker-compose.yml").read_text(encoding="utf-8"))["services"]
AGENT_SERVICES = ("agent", "agent-tools", "dispatcher")


def registry() -> list:
    return build_tool_registry(load_domain_packs(), get_catalogue())


def paths(app) -> set[str]:
    return {route["path"] for route in build_route_map(app)}


def test_every_route_is_a_registered_tool_with_its_permission():
    expected = {(f"{TOOL_PREFIX}/{t.name}", t.permission) for t in registry()}
    actual = {(r["path"], r["permission"]) for r in build_route_map(create_agent_tools_app())}
    assert actual == expected


def test_tool_routes_are_not_on_the_other_apps():
    assert not [p for p in paths(create_app()) if p.startswith(TOOL_PREFIX)]
    assert not [p for p in paths(create_worker_app()) if p.startswith(TOOL_PREFIX)]


def test_tool_app_has_no_execution_route():
    assert not paths(create_agent_tools_app()) & paths(create_worker_app())


def test_tool_call_without_authentication_is_refused():
    client = TestClient(create_agent_tools_app())
    path = f"{TOOL_PREFIX}/{registry()[0].name}"
    assert client.post(path, headers={TENANT_HEADER: str(uuid4())}).status_code == 401


def valid_response(**extra) -> dict:
    return {
        "case_id": str(uuid4()),
        "classification": "DUPLICATE_CAPTURE",
        "root_cause_summary": "Two captures appear linked to one order.",
        "evidence": [],
        "recommended_playbook": None,
        "requires_approval": True,
        "confidence": "high",
        "missing_evidence": [],
        **extra,
    }


def test_agent_response_accepts_the_contract():
    assert AgentResponse.model_validate(valid_response()).requires_approval is True


@pytest.mark.parametrize("field", ["request_id", "approval_id", "execution_id"])
def test_agent_response_cannot_carry_workflow_ids(field):
    with pytest.raises(ValidationError):
        AgentResponse.model_validate(valid_response(**{field: str(uuid4())}))


@pytest.mark.parametrize(("field", "value"), [
    ("confidence", "certain"),
    ("classification", "duplicate capture"),
    ("recommended_playbook", "refund everything"),
    ("root_cause_summary", ""),
    ("evidence", [{"evidence_id": "not-a-uuid", "source_id": str(uuid4()),
                   "record_id": str(uuid4()), "fact": "x"}]),
])
def test_agent_response_fields_are_frozen(field, value):
    with pytest.raises(ValidationError):
        AgentResponse.model_validate(valid_response(**{field: value}))


@pytest.mark.parametrize("field", ["case_id", "classification", "root_cause_summary", "evidence",
                                   "recommended_playbook", "requires_approval", "confidence",
                                   "missing_evidence"])
def test_every_ac_ai_02_field_is_required(field):
    body = valid_response()
    body.pop(field)
    with pytest.raises(ValidationError):
        AgentResponse.model_validate(body)


# AC-AI-02, AC-AI-04, AC-AGENT-02: output must agree with deterministic state

CASE = uuid4()
EVIDENCE, RECORD, SOURCE = uuid4(), uuid4(), uuid4()


def expectations(**changes) -> Expectations:
    values = {"case_id": CASE, "case_types": frozenset({"DUPLICATE_CAPTURE", "STATUS_MISMATCH"}),
              "eligible_playbooks": {"finops:PB-002": True},
              "returned_ids": frozenset({EVIDENCE, RECORD, SOURCE})}
    return Expectations(**(values | changes))


def output(**changes) -> dict:
    grounded = {"case_id": str(CASE), "recommended_playbook": "finops:PB-002",
                "evidence": [{"evidence_id": str(EVIDENCE), "record_id": str(RECORD),
                              "source_id": str(SOURCE), "fact": "capture status"}]}
    return valid_response(**(grounded | changes))


def test_a_grounded_response_is_accepted():
    response = validate_response(output(), expectations())
    assert response.recommended_playbook == "finops:PB-002"


@pytest.mark.parametrize(("changes", "expected", "reason"), [
    ({"request_id": str(uuid4())}, {}, "SCHEMA"),
    ({"case_id": str(uuid4())}, {}, "WRONG_CASE"),
    ({"classification": "MADE_UP"}, {}, "UNREGISTERED_CLASSIFICATION"),
    ({"recommended_playbook": "finops:PB-001"}, {}, "INELIGIBLE_PLAYBOOK"),
    ({}, {"eligible_playbooks": {}}, "INELIGIBLE_PLAYBOOK"),
    ({"requires_approval": False}, {}, "WRONG_APPROVAL_REQUIREMENT"),
    ({"recommended_playbook": None}, {}, "APPROVAL_WITHOUT_PLAYBOOK"),
    ({}, {"returned_ids": frozenset({EVIDENCE, SOURCE})}, "UNRETURNED_EVIDENCE"),
    ({"evidence": [{"evidence_id": str(uuid4()), "record_id": str(RECORD),
                    "source_id": str(SOURCE), "fact": "invented"}]}, {}, "UNRETURNED_EVIDENCE"),
])
def test_an_ungrounded_response_is_refused(changes, expected, reason):
    with pytest.raises(ResponseRefused) as refused:
        validate_response(output(**changes), expectations(**expected))
    assert refused.value.reason == reason


def test_no_playbook_means_nothing_to_approve():
    response = validate_response(
        output(recommended_playbook=None, requires_approval=False), expectations()
    )
    assert response.requires_approval is False


def test_the_agent_package_mirrors_the_frozen_schema():
    packaged = json.loads(
        (REPO / "agent" / "settleiq_agent" / "response_schema.json").read_text(encoding="utf-8")
    )
    ours = AgentResponse.model_json_schema()

    def shape(schema: dict, name: str | None = None) -> tuple:
        node = schema["$defs"][name] if name else schema
        return sorted(node["properties"]), sorted(node["required"]), node["additionalProperties"]

    assert shape(packaged) == shape(ours)
    assert shape(packaged, "EvidenceReference") == shape(ours, "EvidenceReference")
    assert packaged["properties"]["confidence"]["enum"] == ["low", "medium", "high"]
    # Patterns cannot be clamped by the agent, so structured output must carry them.
    assert packaged["properties"]["classification"]["pattern"] == (
        ours["properties"]["classification"]["pattern"])

    def playbook_patterns(schema: dict) -> list[str]:
        branches = schema["properties"]["recommended_playbook"]["anyOf"]
        return [branch["pattern"] for branch in branches if "pattern" in branch]

    assert playbook_patterns(packaged) == playbook_patterns(ours)


def test_agent_service_has_no_database_path():
    agent = SERVICES["agent"]
    assert agent["networks"] == ["agent"]
    assert not [name for name in agent["environment"] if "DATABASE" in name or "DB_" in name]


def test_agent_does_not_receive_the_token_signing_secret():
    assert "AGENT_TOKEN_SECRET" not in SERVICES["agent"]["environment"]


def test_only_the_agent_receives_the_llm_key():
    holders = [n for n, s in SERVICES.items() if "LLM_API_KEY" in (s.get("environment") or {})]
    assert holders == ["agent"]


@pytest.mark.parametrize("name", AGENT_SERVICES)
def test_agent_services_publish_no_port(name):
    assert "ports" not in SERVICES[name]


def test_agent_network_is_limited_to_its_services():
    members = sorted(n for n, s in SERVICES.items() if "agent" in s.get("networks", []))
    assert members == sorted(AGENT_SERVICES)


@pytest.mark.parametrize("compose", ["docker-compose.yml", "deploy/compose.yaml"])
def test_agent_tools_reads_raw_evidence_read_only(compose):
    """get_case_evidence returns hash-checked raw excerpts, so the tool API needs the evidence
    store; it only reads it, and the agent itself never gets it."""
    services = yaml.safe_load((REPO / compose).read_text(encoding="utf-8"))["services"]
    [mount] = [v for v in services["agent-tools"]["volumes"] if "/data/raw-evidence" in v]
    assert mount.endswith(":/data/raw-evidence:ro")
    assert not any("raw" in v for v in services["agent"].get("volumes", []))
