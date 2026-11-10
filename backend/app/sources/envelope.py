"""Generic canonical source-record envelope (spec 7.1). Frozen under task 1.6 (OPEN-04).

Domain-neutral by contract: `attributes` holds arbitrary payload, and no financial
field may be added here (NFR-016). The only fields are the logical fields in §7.1.

Evolution (OPEN-04, decided for this build): the fields are frozen. A new field must be
nullable and additive, and needs a migration plus an integrated spec revision (22.2). There
is no envelope version field: `connector_version` and `mapping_version` pin what a record
means. A breaking change is out of scope. Optional ingestion metadata goes on the raw
evidence (`evidence.RetrievalContext`), and the event and file conventions are in
`conventions.py`.
"""

from typing import Any
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

RECORD_TYPE = r"^[A-Za-z][\w.-]*:[\w./#-]+$"


class SourceRecordEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    tenant_id: UUID
    source_id: UUID
    source_object_id: UUID
    external_id: str | None
    record_type: str = Field(pattern=RECORD_TYPE)
    attributes: dict[str, Any]
    raw_evidence_id: UUID
    occurred_at: AwareDatetime | None
    observed_at: AwareDatetime
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    connector_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    mapping_version: str | None
