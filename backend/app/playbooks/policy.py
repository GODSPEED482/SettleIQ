"""Approval policy and playbook eligibility (spec 9.2, 8.2).

Whether a request needs approval is never request input. A definition either always requires
it, or defers to the tenant's latest recorded policy for that playbook; with no recorded policy
the answer is REQUIRED.

`evaluate` runs one playbook's preconditions over current case evidence and has no side effect,
so the request gateway, execution-stage revalidation and the Agent's recommend and validate
tools all reach the same answer from the same code.
"""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, text

from app.domain_packs.playbooks import Playbook, get_playbooks
from app.domain_packs.registry import resolve_entrypoint
from app.playbooks.evidence import case_snapshot
from app.semantics.store import active_namespaces

DEFAULT_POLICY_VERSION = "default:REQUIRED"


@dataclass(frozen=True)
class ApprovalPolicy:
    required: bool
    version: str
    source: str


def approval_policy(conn: Connection, playbook: Playbook) -> ApprovalPolicy:
    definition = playbook.definition
    if definition.approval_policy == "ALWAYS_REQUIRED":
        return ApprovalPolicy(True, f"definition:{definition.version}", "definition")
    row = conn.execute(
        text(
            """
            SELECT approval_policy, version FROM playbook_approval_policies
            WHERE pack_namespace = :pack AND playbook_id = :playbook
            ORDER BY version DESC LIMIT 1
            """
        ),
        {"pack": playbook.pack.namespace, "playbook": playbook.template.id},
    ).one_or_none()
    if row is None:
        return ApprovalPolicy(True, DEFAULT_POLICY_VERSION, "default")
    return ApprovalPolicy(row.approval_policy == "REQUIRED", f"tenant:{row.version}", "tenant")


def active_playbooks(conn: Connection) -> dict[str, Playbook]:
    """Executable playbooks of the packs active for the tenant in context."""
    active = set(active_namespaces(conn))
    return {key: p for key, p in get_playbooks().items() if p.pack.namespace in active}


def evaluate(conn: Connection, case_id: UUID, playbook: Playbook,
             parameters: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """The playbook's verdict on one case, or None when the case is not visible."""
    snapshot = case_snapshot(conn, case_id)
    if snapshot is None:
        return None
    case_type = snapshot["case"]["case_type"]
    if case_type not in playbook.definition.trigger_case_types:
        return {"eligible": False, "failures": [f"{case_type} does not trigger this playbook"],
                "target": None, "proof": {}, "evidence_ids": []}
    check = resolve_entrypoint(playbook.definition.preconditions)
    return check(snapshot, parameters or {})


def recommend(conn: Connection, case_id: UUID) -> list[dict[str, Any]] | None:
    """Every active playbook this case's type triggers, with its verdict. Eligible ones first."""
    snapshot = case_snapshot(conn, case_id)
    if snapshot is None:
        return None
    out = []
    for key, playbook in sorted(active_playbooks(conn).items()):
        if snapshot["case"]["case_type"] not in playbook.definition.trigger_case_types:
            continue
        result = resolve_entrypoint(playbook.definition.preconditions)(snapshot, {})
        out.append({
            "playbook": key,
            "version": playbook.definition.version,
            "requires_approval": approval_policy(conn, playbook).required,
            **result,
        })
    return sorted(out, key=lambda item: (not item["eligible"], item["playbook"]))
