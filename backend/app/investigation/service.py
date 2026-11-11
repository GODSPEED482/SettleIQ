"""Starting and reading investigations (spec 8.1, AC-INV-01).

Starting one writes the RUNNING investigation and its INVESTIGATION job in one transaction; the
dispatcher runs it. The investigation pins the tool registry version and the case evidence it
started from, so a stored result says what it was produced from.
"""

import json
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.audit import write_audit
from app.authz.catalogue import get_catalogue
from app.authz.guard import TenantContext
from app.db import tenant_tx
from app.domain_packs.manifest import load_domain_packs
from app.errors import conflict, not_found
from app.investigation.tool_impl import registry_version
from app.investigation.tools import build_tool_registry

CLOSED_CASE_STATUSES = {"RESOLVED", "CLOSED", "DISMISSED"}


def current_registry_version() -> str:
    return registry_version(build_tool_registry(load_domain_packs(), get_catalogue()))


def start(ctx: TenantContext, case_id: UUID) -> dict[str, str]:
    investigation_id, job_id = uuid4(), uuid4()
    with tenant_tx(ctx) as conn:
        case = conn.execute(
            text(
                """
                SELECT c.id, c.case_type, c.status, c.entity_ids, c.observation_ids,
                    c.evidence, r.pack_namespace
                FROM domain_cases c JOIN domain_runs r ON r.id = c.run_id
                WHERE c.id = :id
                """
            ),
            {"id": case_id},
        ).one_or_none()
        if case is None:
            raise not_found()
        if case.status in CLOSED_CASE_STATUSES:
            raise conflict("CASE_CLOSED", "A closed case cannot be investigated.")
        agent_id = conn.execute(
            text("SELECT id FROM service_principals WHERE kind = 'agent' AND status = 'ACTIVE'")
        ).scalar_one_or_none()
        if agent_id is None:
            raise conflict("AGENT_UNAVAILABLE", "This tenant has no active Agent principal.")
        evidence = {
            "case_type": case.case_type,
            "case_status": case.status,
            "entity_ids": sorted(str(i) for i in case.entity_ids),
            "observation_ids": sorted(str(i) for i in case.observation_ids),
            "source_record_ids": case.evidence.get("source_record_ids", []),
        }
        try:
            with conn.begin_nested():
                conn.execute(
                    text(
                        """
                        INSERT INTO agent_jobs (id, tenant_id, kind, subject_id, requested_by,
                            payload)
                        VALUES (:id, :tenant, 'INVESTIGATION', :subject, :user,
                            CAST(:payload AS jsonb))
                        """
                    ),
                    {"id": job_id, "tenant": ctx.tenant_id, "subject": investigation_id,
                     "user": ctx.principal.id, "payload": json.dumps({"case_id": str(case_id)})},
                )
                conn.execute(
                    text(
                        """
                        INSERT INTO investigations (id, tenant_id, case_id, pack_namespace,
                            invoked_by, agent_principal_id, job_id, tool_registry_version,
                            input_evidence)
                        VALUES (:id, :tenant, :case, :pack, :user, :agent, :job, :version,
                            CAST(:evidence AS jsonb))
                        """
                    ),
                    {"id": investigation_id, "tenant": ctx.tenant_id, "case": case_id,
                     "pack": case.pack_namespace, "user": ctx.principal.id, "agent": agent_id,
                     "job": job_id, "version": current_registry_version(),
                     "evidence": json.dumps(evidence)},
                )
        except IntegrityError:
            raise conflict("INVESTIGATION_RUNNING",
                           "This case already has a running investigation.") from None
        write_audit(
            conn, ctx.tenant_id, ctx.principal, "investigation.started", "investigation",
            {"investigation_id": str(investigation_id), "case_id": str(case_id),
             "job_id": str(job_id)},
        )
    return {"investigation_id": str(investigation_id), "status": "RUNNING"}


def _iso(value: Any) -> str | None:
    return value.isoformat() if value else None


def list_for(ctx: TenantContext, case_id: UUID | None) -> list[dict[str, Any]]:
    """Summaries, newest first, so a screen can find a case's investigations again."""
    with tenant_tx(ctx) as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, case_id, status, proposed_classification, recommended_playbook,
                    confidence, error, created_at, finished_at
                FROM investigations
                WHERE CAST(:case AS uuid) IS NULL OR case_id = :case
                ORDER BY created_at DESC, id
                LIMIT 200
                """
            ),
            {"case": case_id},
        ).all()
    return [
        {"investigation_id": str(r.id), "case_id": str(r.case_id), "status": r.status,
         "proposed_classification": r.proposed_classification,
         "recommended_playbook": r.recommended_playbook, "confidence": r.confidence,
         "error": r.error, "created_at": _iso(r.created_at), "finished_at": _iso(r.finished_at)}
        for r in rows
    ]


def read(ctx: TenantContext, investigation_id: UUID) -> dict[str, Any]:
    with tenant_tx(ctx) as conn:
        row = conn.execute(
            text(
                """
                SELECT i.*, j.status AS job_status FROM investigations i
                JOIN agent_jobs j ON j.id = i.job_id WHERE i.id = :id
                """
            ),
            {"id": investigation_id},
        ).one_or_none()
        if row is None:
            raise not_found()
        calls = conn.execute(
            text(
                """
                SELECT id, tool_name, tool_version, permission, input, input_hash, output,
                    output_hash, evidence_ids, outcome, error, created_at
                FROM investigation_tool_calls WHERE investigation_id = :id
                ORDER BY created_at, id
                """
            ),
            {"id": investigation_id},
        ).all()
    return {
        "investigation_id": str(row.id),
        "case_id": str(row.case_id),
        "pack_namespace": row.pack_namespace,
        "status": row.status,
        "job_status": row.job_status,
        "invoked_by": str(row.invoked_by),
        "agent_principal_id": str(row.agent_principal_id),
        "model": row.model,
        "prompt_version": row.prompt_version,
        "tool_registry_version": row.tool_registry_version,
        "input_evidence": row.input_evidence,
        # The Agent's classification is a proposal; the case's own type never changes.
        "proposed_classification": row.proposed_classification,
        "recommended_playbook": row.recommended_playbook,
        "confidence": row.confidence,
        "result": row.result,
        "error": row.error,
        "created_at": _iso(row.created_at),
        "finished_at": _iso(row.finished_at),
        "tool_calls": [
            {
                "id": str(c.id),
                "tool": c.tool_name,
                "tool_version": c.tool_version,
                "permission": c.permission,
                "input": c.input,
                "input_hash": c.input_hash,
                "output": c.output,
                "output_hash": c.output_hash,
                "evidence_ids": [str(e) for e in c.evidence_ids],
                "outcome": c.outcome,
                "error": c.error,
                "created_at": _iso(c.created_at),
            }
            for c in calls
        ],
    }
