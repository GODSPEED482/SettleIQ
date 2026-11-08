"""Trusted dispatcher for investigation jobs (spec 8). Owner: Swapnaneel, task 5.7.

The agent holds no database credentials, so this process consumes the queue on its behalf. It
runs under its own database role, whose only cross-tenant access is claiming job rows; all
other work goes through `scoped_tx(job.tenant_id)`.
"""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class ClaimedJob:
    investigation_id: UUID
    tenant_id: UUID
    case_id: UUID
    invoker_id: UUID


def claim_next_job() -> ClaimedJob | None:
    """Claim one job with FOR UPDATE SKIP LOCKED, under the global concurrency cap."""
    raise NotImplementedError("5.7")


def run_job(job: ClaimedJob) -> None:
    """Mint the token, call the agent, validate its output and write the terminal state once."""
    raise NotImplementedError("5.7")


def expire_stale_leases() -> None:
    """Mark jobs whose dispatcher died as FAILED. A retry is a new investigation."""
    raise NotImplementedError("5.7")


def main() -> None:
    raise NotImplementedError("5.7")


if __name__ == "__main__":
    main()
