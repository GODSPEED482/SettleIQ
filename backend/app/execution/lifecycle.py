"""The execution state machine (spec 9.3), the same pairs migration 0016's `guard_execution`
trigger enforces; `test_worker.py` checks the two agree.

The case and execution machines stay separate: a terminal execution only moves its case out of
ACTION_PENDING. A case resolves only after a verified readback (SUCCEEDED); anything else returns
it to HUMAN_REVIEW and never resolves it (spec 9.3, AC-PLY-06).
"""

import json
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, text

from app.errors import conflict

EXECUTION_TRANSITIONS = (
    ("CREATED", "GRANT_ISSUED"),
    ("CREATED", "POLICY_BLOCKED"),
    ("GRANT_ISSUED", "EXECUTING"),
    ("GRANT_ISSUED", "POLICY_BLOCKED"),
    ("GRANT_ISSUED", "FAILED"),
    ("EXECUTING", "VERIFYING"),
    ("EXECUTING", "FAILED"),
    ("EXECUTING", "RETRY_PENDING"),
    ("EXECUTING", "OUTCOME_UNKNOWN"),
    ("RETRY_PENDING", "EXECUTING"),
    ("RETRY_PENDING", "MANUAL_REVIEW"),
    ("OUTCOME_UNKNOWN", "VERIFYING"),
    ("OUTCOME_UNKNOWN", "MANUAL_REVIEW"),
    ("VERIFYING", "SUCCEEDED"),
    ("VERIFYING", "FAILED"),
    ("VERIFYING", "MANUAL_REVIEW"),
)

# Where each terminal execution leaves its case, which is ACTION_PENDING until then.
CASE_AFTER = {
    "SUCCEEDED": "RESOLVED",
    "FAILED": "HUMAN_REVIEW",
    "MANUAL_REVIEW": "HUMAN_REVIEW",
    "POLICY_BLOCKED": "HUMAN_REVIEW",
}
TERMINAL = frozenset(CASE_AFTER)


def can_move(current: str, to: str) -> bool:
    return (current, to) in EXECUTION_TRANSITIONS


def move(conn: Connection, execution_id: UUID, *steps: str, reason: str | None = None,
         result_ref: dict[str, Any] | None = None) -> str:
    """Move an execution through one or more states, in order, in the caller's transaction.
    Reaching a terminal state records its time and moves the case out of ACTION_PENDING."""
    row = conn.execute(
        text("SELECT status, request_id FROM executions WHERE id = :id FOR UPDATE"),
        {"id": execution_id},
    ).one()
    current = row.status
    for to in steps:
        if not can_move(current, to):
            raise conflict("EXECUTION_STATE", f"A {current} execution cannot become {to}.")
        conn.execute(
            text(
                """
                UPDATE executions SET status = :to, updated_at = now(),
                    reason = COALESCE(:reason, reason),
                    result_ref = COALESCE(CAST(:result AS jsonb), result_ref),
                    completed_at = CASE WHEN :terminal THEN now() ELSE completed_at END
                WHERE id = :id
                """
            ),
            {"to": to, "id": execution_id, "reason": reason,
             "result": json.dumps(result_ref) if result_ref is not None else None,
             "terminal": to in TERMINAL},
        )
        current = to
    if current in CASE_AFTER:
        conn.execute(
            text(
                """
                UPDATE domain_cases SET status = :status, updated_at = now()
                WHERE id = (SELECT case_id FROM playbook_requests WHERE id = :request)
                  AND status = 'ACTION_PENDING'
                """
            ),
            {"status": CASE_AFTER[current], "request": row.request_id},
        )
    return current
