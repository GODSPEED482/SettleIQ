"""Case triage and the case lifecycle (spec 9.3, AC-CASE-01, AC-CASE-02).

The case state machine is separate from request and execution state. Triage may move a case
along OPEN -> INVESTIGATING -> HUMAN_REVIEW, dismiss it from any of those, and close a RESOLVED
or DISMISSED case. ACTION_PENDING and RESOLVED belong to the playbook and verified-readback
path, so triage never sets them, and a case with a side effect in flight cannot be dismissed
or closed. Reopen applies only to CLOSED. Dismiss, close and reopen need a reason, and every
change is audited. Migration 0011 enforces the same transitions in the database.
"""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import Connection, text

from app.audit import write_audit
from app.authz.guard import TenantContext
from app.db import tenant_tx
from app.errors import conflict, not_found, validation_failed

TRIAGE_TRANSITIONS = {
    ("OPEN", "INVESTIGATING"),
    ("INVESTIGATING", "HUMAN_REVIEW"),
    ("OPEN", "DISMISSED"),
    ("INVESTIGATING", "DISMISSED"),
    ("HUMAN_REVIEW", "DISMISSED"),
    ("RESOLVED", "CLOSED"),
    ("DISMISSED", "CLOSED"),
}
NEEDS_REASON = {"DISMISSED", "CLOSED"}

Reason = Field(default=None, min_length=1, max_length=2000)


class TriageBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["INVESTIGATING", "HUMAN_REVIEW", "DISMISSED", "CLOSED"] | None = None
    assignee_id: UUID | None = None
    comment: str | None = Reason
    reason: str | None = Reason

    @model_validator(mode="after")
    def _something_to_do(self) -> "TriageBody":
        if self.status is None and self.assignee_id is None and self.comment is None:
            raise ValueError("give a status, an assignee or a comment")
        return self


class ReopenBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=2000)


# `evidence_provenance` is how the case's source records were obtained, so the UI can label
# SYNTHETIC/REFERENCE evidence wherever a case shows it (spec 10.3.1).
_COLUMNS = """
    id, run_id, case_type, reconciliation_scope, severity, status, fingerprint, entity_ids,
    property_iris, relationship_ids, observation_ids, expected, observed, expected_amount,
    observed_amount, currency, evidence, method, rule_version, version_refs, assignee_id,
    reason, created_at, updated_at,
    ARRAY(
        SELECT DISTINCT e.retrieval_context->>'provenance'
        FROM source_records r
        JOIN raw_evidence e ON e.tenant_id = r.tenant_id AND e.id = r.raw_evidence_id
        WHERE r.id = ANY(ARRAY(
            SELECT CAST(item AS uuid)
            FROM jsonb_array_elements_text(domain_cases.evidence->'source_record_ids') item))
          AND e.retrieval_context->>'provenance' IS NOT NULL
        ORDER BY 1
    ) AS evidence_provenance
"""


def _view(row: Any) -> dict[str, Any]:
    out = dict(row._mapping)
    for key in ("id", "run_id", "assignee_id"):
        out[key] = str(out[key]) if out[key] else None
    for key in ("entity_ids", "relationship_ids", "observation_ids"):
        out[key] = [str(item) for item in out[key]]
    for key in ("expected_amount", "observed_amount"):
        out[key] = format(out[key], "f") if out[key] is not None else None
    for key in ("created_at", "updated_at"):
        out[key] = out[key].isoformat()
    return out


def list_cases(
    ctx: TenantContext, status: str | None, case_type: str | None, run_id: UUID | None
) -> list[dict[str, Any]]:
    with tenant_tx(ctx) as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT {_COLUMNS} FROM domain_cases
                WHERE (CAST(:status AS text) IS NULL OR status = :status)
                  AND (CAST(:case_type AS text) IS NULL OR case_type = :case_type)
                  AND (CAST(:run AS uuid) IS NULL OR run_id = :run)
                ORDER BY created_at DESC, id
                LIMIT 500
                """
            ),
            {"status": status, "case_type": case_type, "run": run_id},
        ).all()
    return [_view(row) for row in rows]


def _locked(conn: Connection, case_id: UUID) -> Any:
    row = conn.execute(
        text(f"SELECT {_COLUMNS} FROM domain_cases WHERE id = :id FOR UPDATE"), {"id": case_id}
    ).first()
    if row is None:
        raise not_found()
    return row


def _require_member(conn: Connection, user_id: UUID) -> None:
    found = conn.execute(
        text("SELECT 1 FROM tenant_members WHERE user_id = :u AND status = 'ACTIVE'"),
        {"u": user_id},
    ).first()
    if found is None:
        raise validation_failed("The assignee is not an active member of this tenant.")


def triage(ctx: TenantContext, case_id: UUID, body: TriageBody) -> dict[str, Any]:
    with tenant_tx(ctx) as conn:
        case = _locked(conn, case_id)
        target = body.status
        if target is not None and target != case.status:
            if case.status == "ACTION_PENDING":
                raise conflict(
                    "CASE_ACTION_IN_FLIGHT",
                    "A case with an external side effect in flight cannot change here.",
                )
            if (case.status, target) not in TRIAGE_TRANSITIONS:
                raise conflict(
                    "INVALID_CASE_TRANSITION", f"A {case.status} case cannot become {target}."
                )
            if target in NEEDS_REASON and body.reason is None:
                raise validation_failed(f"A reason is required to make a case {target}.")
        if body.assignee_id is not None:
            _require_member(conn, body.assignee_id)
        conn.execute(
            text(
                """
                UPDATE domain_cases SET
                    status = COALESCE(:status, status),
                    assignee_id = COALESCE(:assignee, assignee_id),
                    reason = COALESCE(:reason, reason),
                    updated_at = now()
                WHERE id = :id
                """
            ),
            {"status": target, "assignee": body.assignee_id, "reason": body.reason,
             "id": case_id},
        )
        write_audit(
            conn, ctx.tenant_id, ctx.principal, "case.triaged", "case",
            {
                "case_id": str(case_id),
                "from": case.status,
                "to": target or case.status,
                "assignee_id": str(body.assignee_id) if body.assignee_id else None,
                "comment": body.comment,
                "reason": body.reason,
            },
        )
        return _view(_locked(conn, case_id))


def reopen(ctx: TenantContext, case_id: UUID, body: ReopenBody) -> dict[str, Any]:
    with tenant_tx(ctx) as conn:
        case = _locked(conn, case_id)
        if case.status != "CLOSED":
            raise conflict("INVALID_CASE_TRANSITION", "Only a CLOSED case can be reopened.")
        duplicate = conn.execute(
            text(
                "SELECT id FROM domain_cases WHERE fingerprint = :f AND id <> :id "
                "AND status NOT IN ('DISMISSED', 'CLOSED')"
            ),
            {"f": case.fingerprint, "id": case_id},
        ).scalar()
        if duplicate is not None:
            raise conflict("CASE_ALREADY_OPEN", "A later run already reopened this finding.")
        conn.execute(
            text(
                "UPDATE domain_cases SET status = 'OPEN', reason = :reason, updated_at = now() "
                "WHERE id = :id"
            ),
            {"reason": body.reason, "id": case_id},
        )
        write_audit(
            conn, ctx.tenant_id, ctx.principal, "case.reopened", "case",
            {"case_id": str(case_id), "from": "CLOSED", "to": "OPEN", "reason": body.reason},
        )
        return _view(_locked(conn, case_id))
