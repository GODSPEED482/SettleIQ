"""Delegated tool tokens (POL-08). Owner: Swapnaneel, task 5.7.

A token carries identity only, so permissions are resolved again on every tool call and
revocation applies mid-run. It is minted when a job starts, so it cannot expire in the queue.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class DelegatedToken:
    investigation_id: UUID
    tenant_id: UUID
    case_id: UUID
    invoker_id: UUID
    expires_at: datetime


def mint_token(token: DelegatedToken) -> str:
    raise NotImplementedError("5.7")


def verify_token(raw: str) -> DelegatedToken:
    raise NotImplementedError("5.7")
