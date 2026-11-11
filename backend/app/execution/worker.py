"""The Worker: one execution under its single-use grant (POL-09; spec 9.3-9.5; task 5.6).

The Worker app authenticates only an execution grant (`app.authz.workers`), so a human never
reaches this code (AC-AUTHZ-17). Then, in order:

1. In one transaction: lock the execution, check that the grant is bound to exactly it, re-check
   the exact scope's mode and POL-14 (the verifier sees this container's own image digest), then
   consume the grant and move the execution to EXECUTING (AC-AUTHZ-08, AC-AUTHZ-15, AC-AUTHZ-16).
   A failing gate blocks the execution and consumes nothing.
2. Record every action attempt durably before its side effect, and its outcome after it, each in
   its own committed transaction (AC-PLY-10). Attempts are append-only, so each phase is a row.
3. Act through the connector's executor, retrying only what its retry policy allows and only
   under the same idempotency key. An unknown outcome is settled by reading state back.
4. VERIFYING: re-read authoritative state independently. Only a matching readback makes the
   execution SUCCEEDED and resolves the case; API success never does (AC-PLY-06).
"""

import json
import logging
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, text

from app.audit import write_audit
from app.authz.guard import TenantContext
from app.connectors.manifest import load_connector_manifests
from app.db import tenant_tx
from app.errors import ApiError, conflict, not_found
from app.execution.executors import Acted, Executor, Job, Refused, Verified, evidence_hash
from app.execution.lifecycle import move
from app.execution.merchant import MerchantCorrection
from app.execution.scope import ExactScope
from app.playbooks.gateway import _release_gate

logger = logging.getLogger(__name__)

EXECUTORS: dict[tuple[str, str], Executor] = {
    ("internal-merchant", "merchant.payment_status.correct"): MerchantCorrection(),
}

# The grant fields that must equal the execution's exactly (spec 9.4).
BINDING = ("request_id", "request_policy_decision_id", "execution_policy_decision_id",
           "approval_id", "playbook_version", "connector_id", "action_capability", "target",
           "idempotency_key")


class Attempts:
    """Append-only attempt rows for one execution, each committed before the next step."""

    def __init__(self, ctx: TenantContext, job: Job) -> None:
        self._ctx, self._job, self._sequence = ctx, job, 0

    def record(self, status: str, *, capability: str | None = None,
               request_evidence: dict[str, Any] | None = None,
               response_evidence: dict[str, Any] | None = None,
               error_class: str | None = None) -> None:
        self._sequence += 1
        with tenant_tx(self._ctx) as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO execution_action_attempts (id, tenant_id, execution_id, sequence,
                        connector_id, action_capability, target, idempotency_key,
                        request_evidence, response_evidence, status, error_class, finished_at)
                    VALUES (:id, :tenant, :execution, :sequence, :connector, :capability,
                        CAST(:target AS jsonb), :key, CAST(:request AS jsonb),
                        CAST(:response AS jsonb), :status, :error,
                        CASE WHEN :status = 'STARTED' THEN NULL ELSE now() END)
                    """
                ),
                {"id": uuid4(), "tenant": self._ctx.tenant_id, "execution": self._job.execution_id,
                 "sequence": self._sequence, "connector": self._job.connector_id,
                 "capability": capability or self._job.capability,
                 "target": _json(self._job.target), "key": self._job.idempotency_key,
                 "request": _json(request_evidence or {}),
                 "response": _json(response_evidence) if response_evidence is not None else None,
                 "status": status, "error": error_class},
            )


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def run(ctx: TenantContext, execution_id: UUID) -> dict[str, Any]:
    """Run one execution under the grant the caller authenticated with, then return its view."""
    if ctx.principal.grant_id is None:
        raise not_found()
    job = _start(ctx, execution_id, ctx.principal.grant_id)
    if job is not None:
        _execute(ctx, job)
    return read(ctx, execution_id)


def _refuse(ctx: TenantContext, execution_id: UUID, error: ApiError,
            detail: dict[str, Any]) -> ApiError:
    """Audit a refusal in its own transaction, so it survives the request failing."""
    with tenant_tx(ctx) as conn:
        write_audit(conn, ctx.tenant_id, ctx.principal, "execution.refused", "execution",
                    {"execution_id": str(execution_id), "reason": error.code, **detail},
                    permission_key="playbook.execute", decision="DENY", policy_rules=["POL-09"])
    return error


def _start(ctx: TenantContext, execution_id: UUID, grant_id: UUID) -> Job | None:
    """Consume the grant and move to EXECUTING, or block, in one transaction."""
    with tenant_tx(ctx) as conn:
        execution = conn.execute(
            text("SELECT * FROM executions WHERE id = :id FOR UPDATE"), {"id": execution_id}
        ).one_or_none()
        grant = conn.execute(
            text("SELECT * FROM execution_grants WHERE id = :id FOR UPDATE"), {"id": grant_id}
        ).one_or_none()
        if execution is None or grant is None or grant.execution_id != execution.id:
            raise not_found()
        mismatched = [name for name in BINDING
                      if getattr(grant, name) != getattr(execution, name)]
        state = execution.status
        if not mismatched and state == "GRANT_ISSUED" and grant.consumed_at is None:
            return _consume(conn, ctx, execution, grant)
    if mismatched:
        raise _refuse(ctx, execution_id,
                      conflict("GRANT_MISMATCH", "The grant is not bound to this execution."),
                      {"grant_id": str(grant_id), "fields": mismatched})
    raise _refuse(ctx, execution_id,
                  conflict("EXECUTION_STATE", f"A {state} execution cannot be run."),
                  {"grant_id": str(grant_id), "status": state})


def _consume(conn: Connection, ctx: TenantContext, execution: Any, grant: Any) -> Job | None:
    scope = ExactScope(ctx.tenant_id, execution.pack_namespace, execution.pack_version,
                       execution.connector_id, execution.action_capability)
    detail = {"execution_id": str(execution.id), "grant_id": str(grant.id),
              "connector_id": execution.connector_id,
              "action_capability": execution.action_capability}
    policy, check, attestation_id = _release_gate(conn, scope)
    if policy is not None:
        move(conn, execution.id, "POLICY_BLOCKED", reason=f"{policy} {check} at the Worker")
        write_audit(conn, ctx.tenant_id, ctx.principal, "execution.blocked", "execution",
                    {**detail, "policy": policy, "check": check},
                    permission_key="playbook.execute", decision="POLICY_BLOCKED",
                    policy_rules=[policy])
        return None
    conn.execute(text("UPDATE execution_grants SET consumed_at = now() WHERE id = :id"),
                 {"id": grant.id})
    conn.execute(
        text("UPDATE executions SET worker_principal_id = :worker, started_at = now() "
             "WHERE id = :id"),
        {"worker": ctx.principal.id, "id": execution.id},
    )
    move(conn, execution.id, "EXECUTING")
    write_audit(conn, ctx.tenant_id, ctx.principal, "execution.started", "execution",
                {**detail, "attestation_id": str(attestation_id),
                 "idempotency_key": execution.idempotency_key},
                permission_key="playbook.execute", decision="ALLOW",
                policy_rules=["POL-06", "POL-09", "POL-14"])
    conditions = conn.execute(
        text("SELECT conditions FROM policy_decisions WHERE id = :id"),
        {"id": execution.request_policy_decision_id},
    ).scalar_one()
    return Job(ctx.tenant_id, execution.id, execution.connector_id,
               execution.action_capability, execution.target, execution.idempotency_key,
               (conditions or {}).get("proof") or {})


def _finish(ctx: TenantContext, job: Job, *steps: str, action: str, detail: dict[str, Any],
            reason: str | None = None, result_ref: dict[str, Any] | None = None,
            permission: str = "playbook.execute") -> None:
    with tenant_tx(ctx) as conn:
        status = move(conn, job.execution_id, *steps, reason=reason, result_ref=result_ref)
        write_audit(conn, ctx.tenant_id, ctx.principal, action, "execution",
                    {"execution_id": str(job.execution_id), "status": status, **detail},
                    permission_key=permission,
                    decision="ALLOW" if status == "SUCCEEDED" else status,
                    policy_rules=["POL-09"])


def _execute(ctx: TenantContext, job: Job) -> None:
    executor = EXECUTORS.get((job.connector_id, job.capability))
    attempts = Attempts(ctx, job)
    if executor is None or not ctx.access.permits("connector.action.invoke"):
        attempts.record("FAILED", error_class="NO_EXECUTOR")
        _finish(ctx, job, "FAILED", action="execution.failed",
                detail={"error_class": "NO_EXECUTOR"}, reason="No executor for this action.")
        return
    try:
        prepared = executor.prepare(job)
    except Refused as refused:
        _refused(ctx, job, attempts, refused)
        return
    except ApiError as exc:
        _refused(ctx, job, attempts, Refused(exc.code))
        return

    acted = _act(ctx, job, executor, attempts, prepared)
    if acted.status == "FAILED":
        _finish(ctx, job, "FAILED", action="execution.failed",
                detail={"error_class": acted.error_class},
                reason=f"The action failed: {acted.error_class}.")
        return
    if acted.status in ("OUTCOME_UNKNOWN", "RETRY"):
        with tenant_tx(ctx) as conn:
            move(conn, job.execution_id, "OUTCOME_UNKNOWN")
        settled = _settle(job, executor)
        attempts.record("SUCCEEDED" if settled.status == "SUCCEEDED" else "OUTCOME_UNKNOWN",
                        response_evidence={"settlement": settled.evidence},
                        error_class=settled.error_class)
        if settled.status != "SUCCEEDED":
            _finish(ctx, job, "MANUAL_REVIEW", action="execution.manual_review",
                    detail={"error_class": settled.error_class},
                    reason="The outcome could not be settled by readback.")
            return
    with tenant_tx(ctx) as conn:
        move(conn, job.execution_id, "VERIFYING")
    _verify(ctx, job, executor, attempts)


def _refused(ctx: TenantContext, job: Job, attempts: Attempts, refused: Refused) -> None:
    attempts.record("FAILED", response_evidence=refused.evidence,
                    error_class=refused.error_class)
    _finish(ctx, job, "FAILED", action="execution.failed",
            detail={"error_class": refused.error_class},
            reason=f"Refused before any side effect: {refused.error_class}.")


def _act(ctx: TenantContext, job: Job, executor: Executor, attempts: Attempts,
         prepared: dict[str, Any]) -> Acted:
    policy = load_connector_manifests()[job.connector_id].retry
    for attempt in range(1, policy.max_attempts + 1):
        attempts.record("STARTED", request_evidence={"prepared": prepared, "attempt": attempt})
        try:
            acted = executor.act(job)
        except Exception:  # noqa: BLE001 - whatever happened, the outcome is not known
            logger.exception("execution %s action raised", job.execution_id)
            acted = Acted("OUTCOME_UNKNOWN", "UNEXPECTED_ERROR")
        recorded = "OUTCOME_UNKNOWN" if acted.status == "RETRY" else acted.status
        attempts.record(recorded, response_evidence=acted.evidence,
                        error_class=acted.error_class)
        if acted.status != "RETRY" or attempt == policy.max_attempts:
            return acted
        with tenant_tx(ctx) as conn:
            move(conn, job.execution_id, "RETRY_PENDING", "EXECUTING")
    raise AssertionError("unreachable")


def _settle(job: Job, executor: Executor) -> Acted:
    try:
        return executor.settle(job)
    except ApiError as exc:
        return Acted("OUTCOME_UNKNOWN", exc.code)
    except Exception:  # noqa: BLE001
        logger.exception("execution %s settlement raised", job.execution_id)
        return Acted("OUTCOME_UNKNOWN", "UNEXPECTED_ERROR")


def _verify(ctx: TenantContext, job: Job, executor: Executor, attempts: Attempts) -> None:
    if not ctx.access.permits("outcome.verify"):
        verified = Verified(False, job.capability, {}, "MISSING_PERMISSION")
    else:
        try:
            verified = executor.verify(job)
        except ApiError as exc:
            verified = Verified(False, job.capability, {}, exc.code)
        except Exception:  # noqa: BLE001
            logger.exception("execution %s readback raised", job.execution_id)
            verified = Verified(False, job.capability, {}, "UNEXPECTED_ERROR")
    attempts.record("VERIFIED" if verified.ok else "FAILED", capability=verified.capability,
                    response_evidence=verified.evidence, error_class=verified.error_class)
    result = {"readback": verified.capability, "evidence": verified.evidence,
              "evidence_hash": evidence_hash(verified.evidence)}
    if verified.ok:
        _finish(ctx, job, "SUCCEEDED", action="execution.verified", detail=result,
                result_ref=result, permission="outcome.verify")
    else:
        _finish(ctx, job, "MANUAL_REVIEW", action="execution.manual_review",
                detail={**result, "error_class": verified.error_class}, result_ref=result,
                reason=f"The readback did not confirm the outcome: {verified.error_class}.",
                permission="outcome.verify")


# Reads


def _iso(value: Any) -> str | None:
    return value.isoformat() if value else None


def view(conn: Connection, execution_id: UUID) -> dict[str, Any] | None:
    execution = conn.execute(
        text("SELECT * FROM executions WHERE id = :id"), {"id": execution_id}
    ).one_or_none()
    if execution is None:
        return None
    attempts = conn.execute(
        text(
            """
            SELECT sequence, connector_id, action_capability, idempotency_key, request_evidence,
                response_evidence, status, error_class, started_at, finished_at
            FROM execution_action_attempts WHERE execution_id = :id ORDER BY sequence
            """
        ),
        {"id": execution_id},
    ).all()
    approval = conn.execute(
        text("SELECT id, approver_user_id, decision, created_at FROM playbook_approvals "
             "WHERE id = :id"),
        {"id": execution.approval_id},
    ).one_or_none() if execution.approval_id else None
    return {
        "id": str(execution.id),
        "request_id": str(execution.request_id),
        "status": execution.status,
        "request_policy_decision_id": str(execution.request_policy_decision_id),
        "execution_policy_decision_id": str(execution.execution_policy_decision_id),
        "approval": None if approval is None else {
            "id": str(approval.id), "approver_user_id": str(approval.approver_user_id),
            "decision": approval.decision, "created_at": _iso(approval.created_at),
        },
        "playbook_id": f"{execution.pack_namespace}:{execution.playbook_id}",
        "connector_id": execution.connector_id,
        "action_capability": execution.action_capability,
        "target": execution.target,
        "idempotency_key": execution.idempotency_key,
        "reason": execution.reason,
        "result_ref": execution.result_ref,
        "created_at": _iso(execution.created_at),
        "started_at": _iso(execution.started_at),
        "completed_at": _iso(execution.completed_at),
        "attempts": [
            {"sequence": a.sequence, "connector_id": a.connector_id,
             "action_capability": a.action_capability, "idempotency_key": a.idempotency_key,
             "request_evidence": a.request_evidence, "response_evidence": a.response_evidence,
             "status": a.status, "error_class": a.error_class,
             "started_at": _iso(a.started_at), "finished_at": _iso(a.finished_at)}
            for a in attempts
        ],
    }


def read(ctx: TenantContext, execution_id: UUID) -> dict[str, Any]:
    with tenant_tx(ctx) as conn:
        found = view(conn, execution_id)
    if found is None:
        raise not_found()
    return found
