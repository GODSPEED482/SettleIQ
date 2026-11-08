"""Validation of agent output (spec 8.3). Owner: Swapnaneel, task 5.8.

Agent output is untrusted until it passes here. The schema drafts the spec's illustrative
contract; unknown fields are refused, so request, approval and execution IDs cannot appear.
"""

from uuid import UUID

from pydantic import BaseModel, ConfigDict


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceReference(_Strict):
    evidence_id: UUID
    source_id: UUID
    record_id: UUID
    fact: str


class AgentResponse(_Strict):
    case_id: UUID
    classification: str
    root_cause_summary: str
    evidence: list[EvidenceReference]
    recommended_playbook: str | None
    requires_approval: bool
    confidence: str
    missing_evidence: list[str]


def check_evidence_references(response: AgentResponse, returned_ids: set[UUID]) -> None:
    """Refuse any evidence ID that no tool returned during this investigation."""
    raise NotImplementedError("5.8")
