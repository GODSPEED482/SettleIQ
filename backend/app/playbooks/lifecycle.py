"""The playbook-request state machine (spec 9.3). Separate from case and execution state.

CREATED -> REQUEST_POLICY_EVALUATED -> NEEDS_APPROVAL -> APPROVED -> PRECONDITIONS_VALIDATED
-> EXECUTION_POLICY_ALLOWED -> EXECUTION_CREATED. When the REQUEST-stage decision needs no
approval the request goes straight from REQUEST_POLICY_EVALUATED to PRECONDITIONS_VALIDATED.
DENIED, REJECTED, EXPIRED and CANCELLED are terminal, and a terminal request returns its case
from ACTION_PENDING to HUMAN_REVIEW, since no side effect has started.

An EXECUTION-stage POLICY_BLOCKED (SHADOW, or no valid POL-14 attestation) is not terminal: the
request stays PRECONDITIONS_VALIDATED, so the same approved request is evaluated again, with a
new decision, once its exact scope is LIVE (spec 9.5, demo steps 10-13).

Migration 0016 enforces the same transitions in the database; test_request_lifecycle.py keeps
the two equal.
"""

from uuid import UUID

from sqlalchemy import Connection, text

from app.errors import conflict

REQUEST_TRANSITIONS = frozenset({
    ("CREATED", "REQUEST_POLICY_EVALUATED"),
    ("REQUEST_POLICY_EVALUATED", "NEEDS_APPROVAL"),
    ("REQUEST_POLICY_EVALUATED", "PRECONDITIONS_VALIDATED"),
    ("REQUEST_POLICY_EVALUATED", "DENIED"),
    ("NEEDS_APPROVAL", "APPROVED"),
    ("NEEDS_APPROVAL", "REJECTED"),
    ("NEEDS_APPROVAL", "EXPIRED"),
    ("NEEDS_APPROVAL", "CANCELLED"),
    ("APPROVED", "PRECONDITIONS_VALIDATED"),
    ("APPROVED", "DENIED"),
    ("APPROVED", "EXPIRED"),
    ("APPROVED", "CANCELLED"),
    ("PRECONDITIONS_VALIDATED", "EXECUTION_POLICY_ALLOWED"),
    ("PRECONDITIONS_VALIDATED", "DENIED"),
    ("PRECONDITIONS_VALIDATED", "EXPIRED"),
    ("PRECONDITIONS_VALIDATED", "CANCELLED"),
    ("EXECUTION_POLICY_ALLOWED", "EXECUTION_CREATED"),
})
TERMINAL = frozenset({"DENIED", "REJECTED", "EXPIRED", "CANCELLED", "EXECUTION_CREATED"})
# Terminal without a side effect: the case goes back for human review.
RETURNS_CASE = frozenset({"DENIED", "REJECTED", "EXPIRED", "CANCELLED"})


def can_advance(current: str, to: str) -> bool:
    return (current, to) in REQUEST_TRANSITIONS


def advance(conn: Connection, request_id: UUID, *steps: str) -> str:
    """Move a request through one or more states, in order, in the caller's transaction."""
    row = conn.execute(
        text("SELECT status, case_id FROM playbook_requests WHERE id = :id FOR UPDATE"),
        {"id": request_id},
    ).one()
    current = row.status
    for to in steps:
        if not can_advance(current, to):
            raise conflict("REQUEST_STATE", f"A {current} request cannot become {to}.")
        conn.execute(
            text("UPDATE playbook_requests SET status = :to, updated_at = now() WHERE id = :id"),
            {"to": to, "id": request_id},
        )
        current = to
    if current in RETURNS_CASE:
        conn.execute(
            text(
                "UPDATE domain_cases SET status = 'HUMAN_REVIEW', updated_at = now() "
                "WHERE id = :case AND status = 'ACTION_PENDING'"
            ),
            {"case": row.case_id},
        )
    return current
