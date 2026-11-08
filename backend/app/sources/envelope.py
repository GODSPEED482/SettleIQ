"""Generic canonical source-record envelope (spec 7.1). DRAFT under OPEN-04.

Domain-neutral by contract: `attributes` holds arbitrary payload, and no financial
field may be added here (NFR-016).
"""

from typing import Any
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


# TODO(G1 OPEN-04 Rahul): settle envelope evolution, then freeze this under task 1.6.
# TODO(G1 1.7 Rahul): persist to source_records; raw evidence is stored and hashed first, and
# raw_evidence_id and payload_hash come from that store.
class SourceRecordEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    tenant_id: UUID
    source_id: UUID
    source_object_id: UUID
    external_id: str | None
    record_type: str = Field(pattern=r"^[A-Za-z][\w.-]*:[\w./#-]+$")
    attributes: dict[str, Any]
    raw_evidence_id: UUID
    occurred_at: AwareDatetime | None
    observed_at: AwareDatetime
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    connector_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    mapping_version: str | None
