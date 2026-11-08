"""Stub contract for the agent service. Task 5.7 replaces these with loop behaviour tests."""

from uuid import uuid4

from fastapi.testclient import TestClient

from settleiq_agent.loop import DEADLINE_SECONDS, MAX_STEPS
from settleiq_agent.main import app

client = TestClient(app)


def body(**extra) -> dict:
    return {"investigation_id": str(uuid4()), "case_id": str(uuid4()), "tool_token": "t", **extra}


def test_run_is_a_declared_stub():
    assert client.post("/run", json=body()).status_code == 501


def test_run_refuses_unknown_fields():
    assert client.post("/run", json=body(tenant_id=str(uuid4()))).status_code == 422


def test_every_run_is_bounded():
    assert MAX_STEPS > 0
    assert DEADLINE_SECONDS > 0
