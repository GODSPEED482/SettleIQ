"""The investigation result the agent returns (spec 8.3). Untrusted until the backend accepts it.

This mirrors the backend's frozen schema (backend/app/investigation/response.py). Length and
reference checks happen there; this model only gives the model a strict shape to fill. Run
`python -m settleiq_agent.contract > settleiq_agent/response_schema.json` after a deliberate
change, and the backend's test_investigation.py will hold the two sides equal.
"""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceReference(_Strict):
    evidence_id: str
    source_id: str
    record_id: str
    fact: str


class InvestigationResult(_Strict):
    case_id: str
    classification: str
    root_cause_summary: str
    evidence: list[EvidenceReference]
    recommended_playbook: str | None
    requires_approval: bool
    confidence: Literal["low", "medium", "high"]
    missing_evidence: list[str]


def schema_json() -> str:
    return json.dumps(InvestigationResult.model_json_schema(), indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    print(schema_json(), end="")  # noqa: T201
