"""Trusted dispatcher for agent jobs (spec 8).

The agent holds no database credentials, so this process consumes the queue on its behalf. It
runs under its own database role, whose only cross-tenant access is claiming job rows; all
other work goes through `scoped_tx(job.tenant_id)`.
"""

import json
import logging
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
from pydantic import ValidationError
from sqlalchemy import text

from app.audit import write_audit
from app.authz.access import Principal, PrincipalKind
from app.authz.catalogue import get_catalogue
from app.config import get_settings
from app.db import global_tx, scoped_tx
from app.domain_packs.manifest import load_domain_packs
from app.investigation.response import Expectations, ResponseRefused, validate_response
from app.investigation.tokens import DelegatedToken, TokenError, mint_token
from app.investigation.tool_impl import tool_specs
from app.investigation.tools import build_tool_registry
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
    except ValidationError as error:
        # Where the output failed, so a refusal can be diagnosed; never the output itself.
        logger.warning("agent proposal refused: %s",
                       [(".".join(map(str, e["loc"])), e["msg"]) for e in error.errors()][:10])
        raise JobFailed("INVALID_AGENT_OUTPUT") from None
    except UngroundedProposal as error:
        logger.warning("agent proposal ungrounded: %s", str(error)[:300])
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


def _investigation(conn: Any, job: ClaimedJob) -> Any:
    return conn.execute(
        text(
            """
            SELECT id, case_id, invoked_by, pack_namespace, status
            FROM investigations WHERE id = :id AND job_id = :job
            """
        ),
        {"id": job.subject_id, "job": job.id},
    ).one_or_none()


def _expectations(conn: Any, run: Any) -> Expectations:
    """What the agent's answer must agree with, read from the server-written trace: the IDs
    tools returned, and the eligibility and approval answers of the dry-run playbook tools."""
    calls = conn.execute(
        text(
            """
            SELECT tool_name, output, evidence_ids FROM investigation_tool_calls
            WHERE investigation_id = :id AND outcome = 'OK' ORDER BY created_at, id
            """
        ),
        {"id": run.id},
    ).all()
    returned: set[UUID] = set()
    eligible: dict[str, bool] = {}
    for call in calls:
        returned.update(call.evidence_ids)
        if call.tool_name == "recommend_playbook":
            for item in call.output.get("eligible", []):
                eligible[item["playbook"]] = bool(item["requires_approval"])
        elif call.tool_name == "validate_playbook" and call.output.get("eligible"):
            eligible[call.output["playbook"]] = bool(call.output["requires_approval"])
    pack = load_domain_packs().get(run.pack_namespace)
    case_types = frozenset(c.code for c in pack.case_types) if pack else frozenset()
    return Expectations(run.case_id, case_types, eligible, frozenset(returned))


def _label(output: dict[str, Any], key: str) -> str:
    value = output.get(key)
    if not isinstance(value, str) or not 0 < len(value) <= 200:
        raise JobFailed("INVALID_AGENT_OUTPUT")
    return value


def _investigate(job: ClaimedJob) -> None:
    """Run one investigation through the agent and make its single terminal write."""
    with scoped_tx(job.tenant_id) as conn:
        run = _investigation(conn, job)
    if run is None or run.status != "RUNNING":
        raise JobFailed("INVESTIGATION_NOT_RUNNING")
    expires = datetime.now(UTC) + timedelta(minutes=LEASE_MINUTES)
    try:
        token = mint_token(
            DelegatedToken(run.id, job.tenant_id, run.case_id, run.invoked_by, expires)
        )
    except TokenError:
        raise JobFailed("TOKEN_UNAVAILABLE") from None
    tools = tool_specs(build_tool_registry(load_domain_packs(), get_catalogue()))
    output = _call_agent("/run", {
        "investigation_id": str(run.id), "case_id": str(run.case_id), "tool_token": token,
        "tools": tools,
    })
    if not isinstance(output, dict):
        raise JobFailed("INVALID_AGENT_OUTPUT")
    model, prompt_version = _label(output, "model"), _label(output, "prompt_version")
    with scoped_tx(job.tenant_id) as conn:
        if not _still_running(conn, job):
            return
        try:
            result = validate_response(output.get("result"), _expectations(conn, run))
        except ResponseRefused as refused:
            # Refused output is never stored; the reason is a fixed code (spec 8.5).
            raise JobFailed(f"REFUSED_{refused.reason}") from None
        conn.execute(
            text(
                """
                UPDATE investigations
                SET status = 'SUCCEEDED', model = :model, prompt_version = :prompt,
                    result = CAST(:result AS jsonb), proposed_classification = :classification,
                    recommended_playbook = :playbook, confidence = :confidence,
                    finished_at = now()
                WHERE id = :id AND status = 'RUNNING'
                """
            ),
            {"id": run.id, "model": model, "prompt": prompt_version,
             "result": result.model_dump_json(), "classification": result.classification,
             "playbook": result.recommended_playbook, "confidence": result.confidence},
        )
        write_audit(
            conn, job.tenant_id, _agent_principal(conn), "investigation.completed",
            "investigation",
            {"investigation_id": str(run.id), "case_id": str(run.case_id),
             "job_id": str(job.id), "proposed_classification": result.classification,
             "recommended_playbook": result.recommended_playbook},
            invoker_user_id=job.requested_by,
        )
        _finish(conn, job, "SUCCEEDED", None)


def _fail_investigation(conn: Any, investigation_id: UUID, reason: str) -> None:
    conn.execute(
        text(
            """
            UPDATE investigations SET status = 'FAILED', error = :reason, finished_at = now()
            WHERE id = :id AND status = 'RUNNING'
            """
        ),
        {"id": investigation_id, "reason": reason},
    )


def _fail(job: ClaimedJob, reason: str) -> None:
    with scoped_tx(job.tenant_id) as conn:
        if job.kind == "INVESTIGATION" and _still_running(conn, job):
            _fail_investigation(conn, job.subject_id, reason)
        _finish(conn, job, "FAILED", reason)


def run_job(job: ClaimedJob) -> None:
    """Call the agent, validate its output and write the terminal state once."""
    try:
        if job.kind == "ONTOLOGY_DRAFT":
            _complete_draft(job, _proposals_for(job))
        elif job.kind == "INVESTIGATION":
            _investigate(job)
        else:
            raise JobFailed("UNSUPPORTED_JOB_KIND")
    except JobFailed as failure:
        _fail(job, failure.reason)


def expire_stale_leases() -> int:
    """Mark jobs whose dispatcher died as FAILED, with their investigations. A retry is a new
    job."""
    with global_tx() as conn:
        expired = conn.execute(
            text(
                """
                UPDATE agent_jobs
                SET status = 'FAILED', error = 'LEASE_EXPIRED', finished_at = now()
                WHERE status = 'RUNNING' AND lease_expires_at < now()
                RETURNING tenant_id, kind, subject_id
                """
            )
        ).all()
    for job in expired:
        if job.kind == "INVESTIGATION":
            with scoped_tx(job.tenant_id) as conn:
                _fail_investigation(conn, job.subject_id, "LEASE_EXPIRED")
    return len(expired)


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
