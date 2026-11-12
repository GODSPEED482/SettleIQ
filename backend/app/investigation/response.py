"""Validation of agent output (spec 8.3, 8.5). Owner: Swapnaneel, task 5.8.

Agent output is untrusted until it passes here, and a refused output is never stored as a
result. The schema is frozen: unknown fields are refused, so request, approval and execution IDs
cannot appear (AC-AGENT-02), and every field AC-AI-02 names is required. Beyond the schema, the
output must agree with deterministic state the agent cannot change:

- `case_id` is the investigated case;
- `classification` is a case type the case's active pack registers;
- `recommended_playbook` is a playbook the deterministic check found eligible for this case,
  and `requires_approval` is that playbook's resolved approval policy (false with none);
- every evidence, record and source ID was returned by a tool during this investigation, so
  evidence is independently retrievable without trusting the model's text (AC-AI-04).

The agent package mirrors this schema in `agent/settleiq_agent/response_schema.json`;
test_investigation.py keeps the two equal.
"""

from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

CASE_TYPE = r"^[A-Z][A-Z0-9_]*$"
PLAYBOOK_ID = r"^[a-z][a-z0-9_]*:[A-Za-z0-9_-]+$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EvidenceReference(_Strict):
    evidence_id: UUID
    source_id: UUID
    record_id: UUID
    fact: str = Field(min_length=1, max_length=500)


class AgentResponse(_Strict):
    case_id: UUID
    classification: str = Field(pattern=CASE_TYPE)
    root_cause_summary: str = Field(min_length=1, max_length=2000)
    evidence: list[EvidenceReference] = Field(max_length=50)
    recommended_playbook: str | None = Field(pattern=PLAYBOOK_ID)
    requires_approval: bool
    # Qualitative until calibrated on labelled cases (spec 8.3).
    confidence: Literal["low", "medium", "high"]
    missing_evidence: list[str] = Field(max_length=20)


class ResponseRefused(ValueError):
    """Agent output that cannot be stored. `reason` is a stable code; it carries no model text."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Expectations:
    """What the deterministic services know about the investigated case."""

    case_id: UUID
    case_types: frozenset[str]
    # Eligible playbooks for this case, each with its resolved approval requirement.
    eligible_playbooks: dict[str, bool] = field(default_factory=dict)
    returned_ids: frozenset[UUID] = frozenset()


def check_evidence_references(response: AgentResponse, returned_ids: set[UUID]) -> None:
    """Refuse any evidence ID that no tool returned during this investigation."""
    for item in response.evidence:
        if {item.evidence_id, item.record_id, item.source_id} - set(returned_ids):
            raise ResponseRefused("UNRETURNED_EVIDENCE")


def validate_response(raw: object, expected: Expectations) -> AgentResponse:
    try:
        response = AgentResponse.model_validate(raw)
    except ValidationError:
        raise ResponseRefused("SCHEMA") from None
    if response.case_id != expected.case_id:
        raise ResponseRefused("WRONG_CASE")
    if response.classification not in expected.case_types:
        raise ResponseRefused("UNREGISTERED_CLASSIFICATION")
    if response.recommended_playbook is None:
        if response.requires_approval:
            raise ResponseRefused("APPROVAL_WITHOUT_PLAYBOOK")
    else:
        if response.recommended_playbook not in expected.eligible_playbooks:
            raise ResponseRefused("INELIGIBLE_PLAYBOOK")
        if response.requires_approval != expected.eligible_playbooks[
            response.recommended_playbook
        ]:
            raise ResponseRefused("WRONG_APPROVAL_REQUIREMENT")
    check_evidence_references(response, set(expected.returned_ids))
    return response
