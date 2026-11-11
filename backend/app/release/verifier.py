"""POL-14 runtime verification (spec 9.7.6). Owner: Rishav, task 5.4.

Every LIVE switch and every execution grant asks this module whether one exact scope holds a
valid attestation. Any failure is a POL-14 denial naming the failing check.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Connection

from app.execution.scope import ExactScope


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    check: str | None = None
    attestation_id: UUID | None = None


def verify(conn: Connection, scope: ExactScope) -> Verdict:
    # TODO(G5 5.4 Rishav): the ten checks of spec 9.7.6 over a stored signed attestation. Until
    # then no attestation can exist, so every scope fails closed at the first check.
    return Verdict(False, "SIGNATURE")
