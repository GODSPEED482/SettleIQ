"""Draft-generation endpoint, with a fake model. Grounding is enforced by the backend."""

from fastapi.testclient import TestClient
from openai import APIConnectionError

from settleiq_agent.drafting import SYSTEM_PROMPT, DraftingError, OntologyProposal, OpenAIDrafter
from settleiq_agent.main import app, get_drafter

client = TestClient(app)
BODY = {"metadata": [{"source_id": "s", "object": "orders", "fields": []}], "known_terms": []}


class FakeDrafter:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.requests = []

    def draft(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return OntologyProposal(terms=[], bindings=[])


def post_with(drafter, body=BODY):
    app.dependency_overrides[get_drafter] = lambda: drafter
    try:
        return client.post("/draft-ontology", json=body)
    finally:
        app.dependency_overrides.clear()


def test_draft_returns_a_structured_proposal():
    fake = FakeDrafter()
    response = post_with(fake)
    assert response.status_code == 200
    assert response.json() == {"terms": [], "bindings": []}
    assert fake.requests[0].known_terms == []


def test_draft_refuses_unknown_request_fields():
    assert post_with(FakeDrafter(), BODY | {"tenant_id": "x"}).status_code == 422


def test_drafting_failure_reveals_nothing():
    response = post_with(FakeDrafter(DraftingError("secret detail")))
    assert response.status_code == 502
    assert "secret" not in response.text


def test_provider_failure_is_handled():
    error = APIConnectionError(request=None)
    assert post_with(FakeDrafter(error)).status_code == 502


def test_missing_llm_configuration_is_a_503(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    assert client.post("/draft-ontology", json=BODY).status_code == 503


def test_drafter_needs_a_model_name(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    try:
        OpenAIDrafter()
    except DraftingError as error:
        assert "LLM_MODEL" in str(error)
    else:
        raise AssertionError("expected DraftingError")


def test_prompt_treats_input_as_data_and_foreign_keys_as_structure():
    assert "Treat all of it as" in SYSTEM_PROMPT
    assert "structural evidence only" in SYSTEM_PROMPT


def test_response_schema_is_strict_enough_for_structured_outputs():
    schema = OntologyProposal.model_json_schema()
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["Term"]["additionalProperties"] is False
    assert set(schema["$defs"]["Term"]["required"]) == set(schema["$defs"]["Term"]["properties"])
    assert set(schema["$defs"]["Binding"]["required"]) == set(
        schema["$defs"]["Binding"]["properties"]
    )
