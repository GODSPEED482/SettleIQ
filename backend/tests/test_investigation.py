"""Agent service boundary (spec 8): the private tool API, output contract and Compose isolation."""

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
from app.investigation.response import AgentResponse
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
