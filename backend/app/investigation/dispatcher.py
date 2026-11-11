"""Trusted dispatcher for agent jobs (spec 8).

The agent holds no database credentials, so this process consumes the queue on its behalf. It
runs under its own database role, whose only cross-tenant access is claiming job rows; all
other work goes through `scoped_tx(job.tenant_id)`.
"""

import json
import logging
import threading
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx
from pydantic import ValidationError
from sqlalchemy import text

from app.audit import write_audit
from app.authz.access import Principal, PrincipalKind
from app.config import get_settings
from app.db import global_tx, scoped_tx
from app.semantics.generation import (
    DiscoveredObject,
    OntologyProposal,
    UngroundedProposal,
    ground_proposal,
)

logger = logging.getLogger("settleiq.dispatcher")

LEASE_MINUTES = 5
POLL_SECONDS = 2
AGENT_TIMEOUT_SECONDS = 150


@dataclass(frozen=True)
class ClaimedJob:
    id: UUID
    tenant_id: UUID
    kind: str
    subject_id: UUID
    requested_by: UUID
    payload: dict[str, Any]


class JobFailed(Exception):
    """A failure safe to show users: a fixed reason, never model output or provider text."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


_CLAIM_SQL = text(
    """
    UPDATE agent_jobs
    SET status = 'RUNNING', started_at = now(),
        lease_expires_at = now() + make_interval(mins => :lease)
    WHERE id = (
        SELECT id FROM agent_jobs WHERE status = 'QUEUED'
        ORDER BY created_at
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    )
    AND (SELECT count(*) FROM agent_jobs WHERE status = 'RUNNING') < :cap
    RETURNING id, tenant_id, kind, subject_id, requested_by, payload
    """
)


def claim_next_job() -> ClaimedJob | None:
    """Claim one job with FOR UPDATE SKIP LOCKED, under the global concurrency cap."""
    with global_tx() as conn:
        row = conn.execute(
            _CLAIM_SQL, {"lease": LEASE_MINUTES, "cap": get_settings().agent_job_concurrency}
        ).first()
    if row is None:
        return None
    return ClaimedJob(
        row.id, row.tenant_id, row.kind, row.subject_id, row.requested_by, row.payload
    )


def _call_agent(path: str, body: dict[str, Any]) -> Any:
    try:
        response = httpx.post(
            f"{get_settings().agent_url}{path}", json=body, timeout=AGENT_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, json.JSONDecodeError) as error:
        logger.warning("agent call failed: %s", type(error).__name__)
        raise JobFailed("AGENT_UNAVAILABLE") from None


def _proposals_for(job: ClaimedJob) -> list[dict[str, Any]]:
    metadata = [DiscoveredObject(**item) for item in job.payload["metadata"]]
    output = _call_agent("/draft-ontology", job.payload)
    try:
        return ground_proposal(
            OntologyProposal.model_validate(output), metadata, job.payload["known_terms"]
        )
    except ValidationError:
        raise JobFailed("INVALID_AGENT_OUTPUT") from None
    except UngroundedProposal:
        raise JobFailed("UNGROUNDED_AGENT_OUTPUT") from None


def _still_running(conn: Any, job: ClaimedJob) -> bool:
    """False if the lease expired and this job has already been marked failed."""
    row = conn.execute(
        text("SELECT status FROM agent_jobs WHERE id = :id FOR UPDATE"), {"id": job.id}
    ).first()
    return row is not None and row.status == "RUNNING"


def _finish(conn: Any, job: ClaimedJob, status: str, error: str | None) -> None:
    conn.execute(
        text(
            """
            UPDATE agent_jobs SET status = :status, error = :error, finished_at = now()
            WHERE id = :id AND status = 'RUNNING'
            """
        ),
        {"id": job.id, "status": status, "error": error},
    )


def _agent_principal(conn: Any) -> Principal:
    agent_id = conn.execute(
        text("SELECT id FROM service_principals WHERE kind = 'agent' AND status = 'ACTIVE'")
    ).scalar_one()
    return Principal(agent_id, PrincipalKind.AGENT)


def _complete_draft(job: ClaimedJob, proposals: list[dict[str, Any]]) -> None:
    """Attach proposals and finish the job together. The draft content itself is not changed."""
    with scoped_tx(job.tenant_id) as conn:
        if not _still_running(conn, job):
            return
        updated = conn.execute(
            text(
                """
                UPDATE ontology_drafts
                SET proposals = CAST(:proposals AS jsonb), updated_at = now()
                WHERE id = :draft AND status = 'OPEN'
                """
            ),
            {"draft": job.subject_id, "proposals": json.dumps(proposals)},
        )
        if updated.rowcount != 1:
            _finish(conn, job, "FAILED", "DRAFT_CLOSED")
            return
        write_audit(
            conn, job.tenant_id, _agent_principal(conn), "ontology.generation.completed",
            "ontology",
            {"draft_id": str(job.subject_id), "job_id": str(job.id), "proposals": len(proposals)},
            invoker_user_id=job.requested_by,
        )
        _finish(conn, job, "SUCCEEDED", None)


def _fail(job: ClaimedJob, reason: str) -> None:
    with scoped_tx(job.tenant_id) as conn:
        _finish(conn, job, "FAILED", reason)


def run_job(job: ClaimedJob) -> None:
    """Call the agent, validate its output and write the terminal state once."""
    try:
        if job.kind != "ONTOLOGY_DRAFT":
            # Investigations join this queue under task 5.7.
            # TODO(G5 5.7 Swapnaneel): for an INVESTIGATION job, mint the delegated token,
            # call the agent's /run, validate the output with response.py (5.8) and make the
            # single terminal write; a refused output is FAILED, never stored as a result.
            raise JobFailed("UNSUPPORTED_JOB_KIND")
        _complete_draft(job, _proposals_for(job))
    except JobFailed as failure:
        _fail(job, failure.reason)


def expire_stale_leases() -> int:
    """Mark jobs whose dispatcher died as FAILED. A retry is a new job."""
    with global_tx() as conn:
        expired = conn.execute(
            text(
                """
                UPDATE agent_jobs
                SET status = 'FAILED', error = 'LEASE_EXPIRED', finished_at = now()
                WHERE status = 'RUNNING' AND lease_expires_at < now()
                """
            )
        )
    return expired.rowcount


def main() -> None:
    stop = threading.Event()
    while not stop.is_set():
        expire_stale_leases()
        job = claim_next_job()
        if job is None:
            stop.wait(POLL_SECONDS)
            continue
        run_job(job)


if __name__ == "__main__":
    main()
