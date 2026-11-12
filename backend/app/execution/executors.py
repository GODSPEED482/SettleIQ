"""What the Worker needs from a connector action, one executor per (connector, capability).

An executor never decides authority: the Worker has already consumed the grant and re-checked
the exact scope before `prepare` runs. Each step reports what happened and the Worker records
it, moves the execution and, only after `verify` reads authoritative state back, resolves the
case (spec 9.3, 9.4).

- `prepare` reads whatever the action needs and refuses, before any side effect, a target that
  no longer qualifies by raising `Refused`. What it returns is recorded as request evidence.
- `act` performs the side effect once, under the execution's idempotency key.
- `settle` is called only after an unknown outcome. It may read state, and may repeat the call
  under the same idempotency key, never a new one (no blind retry).
- `verify` re-reads authoritative state independently of the action's response.
"""

import hashlib
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol
from uuid import UUID

import rfc8785

ActStatus = Literal["SUCCEEDED", "FAILED", "OUTCOME_UNKNOWN", "RETRY"]


def evidence_hash(evidence: Any) -> str:
    return hashlib.sha256(rfc8785.dumps(evidence)).hexdigest()


class Refused(Exception):
    """The target no longer qualifies; nothing was changed."""

    def __init__(self, error_class: str, evidence: dict[str, Any] | None = None) -> None:
        super().__init__(error_class)
        self.error_class = error_class
        self.evidence = evidence or {}


@dataclass(frozen=True)
class Acted:
    """`RETRY` is a failure the connector's retry policy allows repeating under the same key."""

    status: ActStatus
    error_class: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Verified:
    ok: bool
    capability: str
    evidence: dict[str, Any] = field(default_factory=dict)
    error_class: str | None = None


@dataclass
class Job:
    """One execution as the Worker runs it. `state` carries what one step leaves the next."""

    tenant_id: UUID
    execution_id: UUID
    connector_id: str
    capability: str
    target: dict[str, Any]
    idempotency_key: str
    proof: dict[str, Any]
    state: dict[str, Any] = field(default_factory=dict)


class Executor(Protocol):
    def prepare(self, job: Job) -> dict[str, Any]: ...

    def act(self, job: Job) -> Acted: ...

    def settle(self, job: Job) -> Acted: ...

    def verify(self, job: Job) -> Verified: ...
