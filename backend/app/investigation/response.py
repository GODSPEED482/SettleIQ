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


# TODO(G5 5.8 Swapnaneel): freeze this schema against spec 8.3 and AC-AI-02: classification is
# a case type the active pack registers, recommended_playbook an eligible template ID, and no
# request, approval or execution ID can appear (AC-AGENT-02). Refuse any evidence ID that no
# tool returned in this investigation. Keep the prohibited-tool check in tools.py failing
# startup (AC-AGENT-01).
def check_evidence_references(response: AgentResponse, returned_ids: set[UUID]) -> None:
    """Refuse any evidence ID that no tool returned during this investigation."""
    raise NotImplementedError("5.8")
