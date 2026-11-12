"""The two-stage policy gateway (spec 6.3, 9.3-9.5; POL-01, POL-06, POL-09, POL-11).
Owner: Rishav, task 5.2.

A request is created with its immutable REQUEST-stage decision, taken from the definition and
the tenant's recorded policy and never from request input, and moves its case to
ACTION_PENDING in the same transaction. An approval answers that decision and is never given
by the requester. The execution stage then revalidates everything the approval relied on
(the playbook and pack versions, the connector action, the case, preconditions and the exact
target), checks the exact scope's mode, and writes a separate immutable EXECUTION-stage
decision:

- DENY when revalidation fails: the request is DENIED and the case returns to HUMAN_REVIEW.
- POLICY_BLOCKED in SHADOW (POL-06): the request stays PRECONDITIONS_VALIDATED and is evaluated
  again, with a new decision, when that exact scope is switched to LIVE.
- ALLOW: the execution row is created in the same transaction. After commit the grant is
  issued in its own transaction, which checks the mode again (spec 9.5), stores only the
  token's hash and hands the token to the Worker dispatch hook.

Each decision is audited as critical with the permission and policy IDs it applied.
"""

import hashlib
import json
import logging
import os
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection, text
from sqlalchemy.exc import IntegrityError

from app.audit import write_audit
from app.authz.access import Scope
from app.authz.guard import TenantContext
from app.db import tenant_tx
from app.domain_packs.playbooks import Playbook, PlaybookDefinition
from app.errors import ApiError, conflict, missing_permission, not_found, validation_failed
from app.execution.mode import resolve_mode
from app.execution.scope import ExactScope, playbook_scope
from app.playbooks.lifecycle import advance
from app.playbooks.policy import active_playbooks, approval_policy, evaluate

logger = logging.getLogger(__name__)

GRANT_TTL = timedelta(minutes=15)
# The Worker acts, records and verifies under the one grant (spec 4.4.1, POL-09).
GRANT_PERMISSIONS = ["playbook.execute", "connector.action.invoke", "execution.record",
                     "outcome.verify"]
WORKER_TIMEOUT_S = 60.0
GATEWAY_VERSION = "gateway/1"
# Request states the execution stage may evaluate. NEEDS_APPROVAL waits for a person.
EVALUABLE = frozenset({"REQUEST_POLICY_EVALUATED", "APPROVED", "PRECONDITIONS_VALIDATED"})

Reason = Field(min_length=1, max_length=2000)


class RequestBody(BaseModel):
    """The case and the playbook's declared parameters. Nothing here can name a tenant, a
    principal, an approval requirement, a decision, an execution or a grant (spec 12.1)."""

    model_config = ConfigDict(extra="forbid")

    case_id: UUID
    parameters: dict[str, Any] = {}


class ApproveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, min_length=1, max_length=2000)


class RejectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Reason


@dataclass(frozen=True)
class ExecutionDispatch:
    """What the Worker needs to start one execution. The token exists only here."""

    tenant_id: UUID
    execution_id: UUID
    grant_id: UUID
    token: str
    expires_at: datetime


def _to_worker(item: ExecutionDispatch) -> None:
    """Hand the grant to the Worker, which consumes it atomically with the move to EXECUTING.

    The token travels only in this call. If the Worker cannot be reached the grant expires
    unused and the execution stays GRANT_ISSUED, visible on the execution read; nothing runs."""
    base = os.environ.get("WORKER_URL")
    if not base:
        logger.warning("execution %s granted; WORKER_URL is not set", item.execution_id)
        return
    try:
        response = httpx.post(
            f"{base.rstrip('/')}/internal/executions/{item.execution_id}/run",
            headers={"Authorization": f"Bearer {item.token}",
                     "X-Tenant-Id": str(item.tenant_id)},
            timeout=WORKER_TIMEOUT_S,
        )
    except httpx.HTTPError:
        logger.warning("execution %s could not reach the Worker", item.execution_id)
        return
    if response.status_code != 200:
        logger.warning("execution %s: Worker answered %s", item.execution_id,
                       response.status_code)


dispatch: Callable[[ExecutionDispatch], None] = _to_worker


# Reads


def _qualified(row: Any) -> str:
    return f"{row.pack_namespace}:{row.playbook_id}"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _view(conn: Connection, request_id: UUID) -> dict[str, Any] | None:
    row = conn.execute(
        text("SELECT * FROM playbook_requests WHERE id = :id"), {"id": request_id}
    ).one_or_none()
    if row is None:
        return None
    decisions = conn.execute(
        text(
            """
            SELECT id, stage, parent_decision_id, policy_version, approval_required, decision,
                conditions, reason, created_at
            FROM policy_decisions WHERE request_id = :id
            ORDER BY stage = 'EXECUTION', created_at, id
            """
        ),
        {"id": request_id},
    ).all()
    approval = conn.execute(
        text(
            """
            SELECT id, request_policy_decision_id, approver_user_id, decision, reason, created_at
            FROM playbook_approvals WHERE request_id = :id
            """
        ),
        {"id": request_id},
    ).one_or_none()
    execution = conn.execute(
        text(
            """
            SELECT id, status, request_policy_decision_id, execution_policy_decision_id,
                approval_id, idempotency_key, created_at
            FROM executions WHERE request_id = :id
            """
        ),
        {"id": request_id},
    ).one_or_none()
    return {
        "id": str(row.id),
        "case_id": str(row.case_id),
        "playbook_id": _qualified(row),
        "playbook_version": row.playbook_version,
        "pack_version": row.pack_version,
        "connector_id": row.connector_id,
        "action_capability": row.action_capability,
        "requested_by": str(row.requested_by),
        "parameters": row.parameters,
        "target": row.target,
        "evidence_refs": row.evidence_refs,
        "status": row.status,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
        "decisions": [
            {
                "id": str(d.id),
                "stage": d.stage,
                "parent_decision_id": str(d.parent_decision_id) if d.parent_decision_id else None,
                "policy_version": d.policy_version,
                "approval_required": d.approval_required,
                "decision": d.decision,
                "conditions": d.conditions,
                "reason": d.reason,
                "created_at": _iso(d.created_at),
            }
            for d in decisions
        ],
        "approval": None if approval is None else {
            "id": str(approval.id),
            "request_policy_decision_id": str(approval.request_policy_decision_id),
            "approver_user_id": str(approval.approver_user_id),
            "decision": approval.decision,
            "reason": approval.reason,
            "created_at": _iso(approval.created_at),
        },
        "execution": None if execution is None else {
            "id": str(execution.id),
            "status": execution.status,
            "request_policy_decision_id": str(execution.request_policy_decision_id),
            "execution_policy_decision_id": str(execution.execution_policy_decision_id),
            "approval_id": str(execution.approval_id) if execution.approval_id else None,
            "idempotency_key": execution.idempotency_key,
            "created_at": _iso(execution.created_at),
        },
    }


def _for_viewer(ctx: TenantContext, conn: Connection, view: dict[str, Any]) -> dict[str, Any]:
    """What only the reader's own request can say: whether they made it, so a screen can hide
    approve and reject from the requester (POL-01 still refuses on the server), and the audit
    records written for it and its execution."""
    execution = view["execution"]["id"] if view["execution"] else None
    audit = conn.execute(
        text(
            """
            SELECT id, action, decision, created_at FROM audit_logs
            WHERE detail ->> 'request_id' = :request
               OR (CAST(:execution AS text) IS NOT NULL AND detail ->> 'execution_id' = :execution)
            ORDER BY created_at, id
            """
        ),
        {"request": view["id"], "execution": execution},
    ).all()
    return view | {
        "requested_by_me": view["requested_by"] == str(ctx.principal.id),
        "audit_refs": [{"id": str(a.id), "action": a.action, "decision": a.decision,
                        "created_at": _iso(a.created_at)} for a in audit],
    }


def read(ctx: TenantContext, request_id: UUID) -> dict[str, Any]:
    with tenant_tx(ctx) as conn:
        view = _view(conn, request_id)
        if view is None:
            raise not_found()
        return _for_viewer(ctx, conn, view)


def list_requests(ctx: TenantContext, case_id: UUID | None,
                  status: str | None) -> list[dict[str, Any]]:
    """Requests newest first, leaving out any playbook the reader is denied (POL-07)."""
    with tenant_tx(ctx) as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, pack_namespace, playbook_id FROM playbook_requests
                WHERE (CAST(:case AS uuid) IS NULL OR case_id = :case)
                  AND (CAST(:status AS text) IS NULL OR status = :status)
                ORDER BY created_at DESC, id
                LIMIT 200
                """
            ),
            {"case": case_id, "status": status},
        ).all()
        return [
            _for_viewer(ctx, conn, _view(conn, row.id)) for row in rows
            if ctx.access.permits("playbook.read", Scope("playbook", _qualified(row)))
        ]


# Request stage


def _parameters(definition: PlaybookDefinition, supplied: dict[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(supplied) - set(definition.parameters))
    if unknown:
        raise validation_failed(f"Undeclared parameters: {', '.join(unknown)}.")
    for name, declared in definition.parameters.items():
        if name not in supplied:
            if declared.required:
                raise validation_failed(f"Parameter {name} is required.")
            continue
        value = supplied[name]
        valid = {
            "string": isinstance(value, str) and len(value) <= declared.max_length,
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "boolean": isinstance(value, bool),
        }[declared.type]
        if not valid:
            raise validation_failed(f"Parameter {name} must be a {declared.type}.")
    return dict(supplied)


def execution_key(playbook: Playbook, target: dict[str, Any]) -> str | None:
    """One key per logical operation: the playbook and its idempotency target fields, so the
    same target can never be acted on twice whatever request asks (spec 9.4)."""
    fields = {name: target.get(name) for name in playbook.definition.idempotency.target_fields}
    if any(value in (None, "") for value in fields.values()):
        return None
    digest = hashlib.sha256(
        json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f"{playbook.qualified_id}:{digest}"


def _scope_detail(scope: ExactScope | None) -> dict[str, Any]:
    if scope is None:
        return {}
    return {"domain_pack_id": scope.domain_pack_id,
            "domain_pack_version": scope.domain_pack_version,
            "connector_id": scope.connector_id, "action_capability": scope.action_capability}


def _require_scoped(ctx: TenantContext, permission: str, qualified_id: str) -> None:
    """The route checked the permission tenant-wide; a grant narrowed to other playbooks, or a
    deny on this one, still refuses (POL-07)."""
    if not ctx.access.permits(permission, Scope("playbook", qualified_id)):
        raise missing_permission(permission)


def _refuse(ctx: TenantContext, action: str, resource: str, permission: str,
            policy: str, detail: dict[str, Any], error: ApiError) -> ApiError:
    """Audit a refusal in its own transaction, so it survives the request failing."""
    with tenant_tx(ctx) as conn:
        write_audit(conn, ctx.tenant_id, ctx.principal, action, resource, detail,
                    permission_key=permission, decision="DENY", policy_rules=[policy])
    return error


def create(ctx: TenantContext, playbook_id: str, body: RequestBody,
           idempotency_key: str) -> tuple[dict[str, Any], bool]:
    """The request and whether it was created now. A repeated key returns the prior request
    (AC-PLY-04); the same key for a different intent is a conflict."""
    try:
        request_id, created, evaluate_now = _create(ctx, playbook_id, body, idempotency_key)
    except IntegrityError:
        # A concurrent create with the same key won the race; return what it wrote.
        request_id, created, evaluate_now = _create(ctx, playbook_id, body, idempotency_key)
    if evaluate_now:
        execution_stage(ctx, request_id)
    return read(ctx, request_id), created


def _create(ctx: TenantContext, playbook_id: str, body: RequestBody,
            idempotency_key: str) -> tuple[UUID, bool, bool]:
    with tenant_tx(ctx) as conn:
        prior = conn.execute(
            text(
                """
                SELECT id, case_id, pack_namespace, playbook_id, parameters
                FROM playbook_requests WHERE idempotency_key = :key
                """
            ),
            {"key": idempotency_key},
        ).one_or_none()
        if prior is not None:
            if (prior.case_id, _qualified(prior), prior.parameters) != (
                    body.case_id, playbook_id, body.parameters):
                raise conflict("IDEMPOTENCY_KEY_REUSED",
                               "This Idempotency-Key was used for a different request.")
            return prior.id, False, False
        playbook = active_playbooks(conn).get(playbook_id)
        if playbook is None:
            raise not_found()
        parameters = _parameters(playbook.definition, body.parameters)
        status = conn.execute(
            text("SELECT status FROM domain_cases WHERE id = :id FOR UPDATE"),
            {"id": body.case_id},
        ).scalar_one_or_none()
        if status is None:
            raise not_found()
        if status != "HUMAN_REVIEW":
            raise conflict("CASE_STATE", f"A {status} case cannot take a playbook request.")
        verdict = evaluate(conn, body.case_id, playbook, parameters)
        failures = [] if verdict["eligible"] else list(verdict["failures"])
        scope = None
        if not failures and execution_key(playbook, verdict["target"] or {}) is None:
            failures.append("The target lacks the fields its idempotency key needs.")
        if not failures:
            try:
                scope = playbook_scope(conn, ctx.tenant_id, playbook)
            except ApiError:
                failures.append("No connector declares this playbook's action.")
        if scope is not None:
            required = _write_request(conn, ctx, playbook, body.case_id, parameters, verdict,
                                      scope, idempotency_key)
            return required[0], True, not required[1]
    raise _refuse(
        ctx, "playbook.request_refused", "playbook_request", "playbook.request", "POL-11",
        {"playbook_id": playbook_id, "case_id": str(body.case_id), "failures": failures},
        ApiError(422, "PLAYBOOK_INELIGIBLE",
                 "The playbook's trigger or preconditions do not hold for this case.",
                 failures=failures),
    )


def _write_request(conn: Connection, ctx: TenantContext, playbook: Playbook, case_id: UUID,
                   parameters: dict[str, Any], verdict: dict[str, Any], scope: ExactScope,
                   idempotency_key: str) -> tuple[UUID, bool]:
    """The request, its REQUEST-stage decision and the case moved to ACTION_PENDING, in the
    caller's transaction. Returns the request ID and whether approval is required."""
    request_id, decision_id = uuid4(), uuid4()
    policy = approval_policy(conn, playbook)
    conn.execute(
        text(
            """
            INSERT INTO playbook_requests (id, tenant_id, case_id, pack_namespace,
                pack_version, playbook_id, playbook_version, connector_id, action_capability,
                requested_by, parameters, target, evidence_refs, idempotency_key)
            VALUES (:id, :tenant, :case, :pack, :pack_version, :playbook, :version,
                :connector, :capability, :user, CAST(:parameters AS jsonb),
                CAST(:target AS jsonb), CAST(:evidence AS jsonb), :key)
            """
        ),
        {"id": request_id, "tenant": ctx.tenant_id, "case": case_id,
         "pack": playbook.pack.namespace, "pack_version": playbook.pack.version,
         "playbook": playbook.template.id, "version": playbook.definition.version,
         "connector": scope.connector_id, "capability": scope.action_capability,
         "user": ctx.principal.id, "parameters": json.dumps(parameters),
         "target": json.dumps(verdict["target"]),
         "evidence": json.dumps(verdict.get("evidence_ids", [])), "key": idempotency_key},
    )
    decision = "NEEDS_APPROVAL" if policy.required else "ALLOW_PENDING_PRECONDITIONS"
    conn.execute(
        text(
            """
            INSERT INTO policy_decisions (id, tenant_id, request_id, stage, policy_version,
                approval_required, decision, conditions, reason)
            VALUES (:id, :tenant, :request, 'REQUEST', :version, :required, :decision,
                CAST(:conditions AS jsonb), :reason)
            """
        ),
        {"id": decision_id, "tenant": ctx.tenant_id, "request": request_id,
         "version": policy.version, "required": policy.required, "decision": decision,
         "conditions": json.dumps({
             "approval_policy_source": policy.source,
             "preconditions": {"eligible": True, "failures": []},
             "proof": verdict.get("proof", {}),
             "scope": _scope_detail(scope),
         }),
         "reason": ("Approval is required by "
                    f"{'the definition' if policy.source == 'definition' else 'policy'}."
                    if policy.required else "The tenant's policy needs no approval.")},
    )
    steps = ["REQUEST_POLICY_EVALUATED"] + (["NEEDS_APPROVAL"] if policy.required else [])
    advance(conn, request_id, *steps)
    conn.execute(
        text(
            "UPDATE domain_cases SET status = 'ACTION_PENDING', updated_at = now() "
            "WHERE id = :id"
        ),
        {"id": case_id},
    )
    write_audit(
        conn, ctx.tenant_id, ctx.principal, "playbook.requested", "playbook_request",
        {"request_id": str(request_id), "case_id": str(case_id),
         "playbook_id": playbook.qualified_id, "request_policy_decision_id": str(decision_id),
         "decision": decision, "approval_policy": policy.version},
        permission_key="playbook.request", decision=decision, policy_rules=["POL-11"],
    )
    return request_id, policy.required


# Approval


def _locked_request(conn: Connection, request_id: UUID) -> Any:
    row = conn.execute(
        text("SELECT * FROM playbook_requests WHERE id = :id FOR UPDATE"), {"id": request_id}
    ).one_or_none()
    if row is None:
        raise not_found()
    return row


def _request_decision(conn: Connection, request_id: UUID) -> Any:
    return conn.execute(
        text("SELECT * FROM policy_decisions WHERE request_id = :id AND stage = 'REQUEST'"),
        {"id": request_id},
    ).one()


def _decide(ctx: TenantContext, request_id: UUID, outcome: str, permission: str,
            body_reason: str | None) -> None:
    """Approve or reject one request that needs approval, by someone other than its
    requester (POL-01)."""
    action = "approved" if outcome == "APPROVED" else "rejected"
    with tenant_tx(ctx) as conn:
        request = _locked_request(conn, request_id)
        qualified = _qualified(request)
        _require_scoped(ctx, permission, qualified)
        requester = request.requested_by
        status = request.status
        if requester != ctx.principal.id and status == "NEEDS_APPROVAL":
            decided = _request_decision(conn, request_id)
            approval_id = uuid4()
            conn.execute(
                text(
                    """
                    INSERT INTO playbook_approvals (id, tenant_id, request_id,
                        request_policy_decision_id, approver_user_id, decision, reason,
                        policy_version)
                    VALUES (:id, :tenant, :request, :decision_id, :user, :outcome, :reason,
                        :version)
                    """
                ),
                {"id": approval_id, "tenant": ctx.tenant_id, "request": request_id,
                 "decision_id": decided.id, "user": ctx.principal.id, "outcome": outcome,
                 "reason": body_reason, "version": decided.policy_version},
            )
            advance(conn, request_id, outcome)
            write_audit(
                conn, ctx.tenant_id, ctx.principal, f"playbook.{action}", "playbook_request",
                {"request_id": str(request_id), "playbook_id": qualified,
                 "request_policy_decision_id": str(decided.id), "approval_id": str(approval_id),
                 "reason": body_reason},
                permission_key=permission, decision="ALLOW", policy_rules=["POL-01"],
            )
            return
    if requester == ctx.principal.id:
        raise _refuse(
            ctx, f"playbook.{action[:-1]}_denied", "playbook_request", permission, "POL-01",
            {"request_id": str(request_id), "playbook_id": qualified,
             "reason": "the requester cannot decide their own request"},
            ApiError(403, "POLICY_DENIED",
                     "The requester cannot approve or reject their own request.",
                     policy="POL-01"),
        )
    raise conflict("REQUEST_STATE", f"A {status} request cannot be {action}.")


def approve(ctx: TenantContext, request_id: UUID, body: ApproveBody | None) -> dict[str, Any]:
    _decide(ctx, request_id, "APPROVED", "playbook.approve", body.reason if body else None)
    execution_stage(ctx, request_id)
    return read(ctx, request_id)


def reject(ctx: TenantContext, request_id: UUID, body: RejectBody) -> dict[str, Any]:
    _decide(ctx, request_id, "REJECTED", "playbook.reject", body.reason)
    return read(ctx, request_id)


# Execution stage


def _revalidate(conn: Connection, tenant_id: UUID,
                request: Any) -> tuple[list[str], ExactScope | None, Playbook | None]:
    """Everything the approval relied on, read again from current state (POL-11, AC-REL-11)."""
    playbook = active_playbooks(conn).get(_qualified(request))
    if playbook is None:
        return ["The playbook's domain pack is no longer active."], None, None
    failures = []
    if playbook.definition.version != request.playbook_version:
        failures.append("The playbook definition changed after the request.")
    if playbook.pack.version != request.pack_version:
        failures.append("The domain pack version changed after the request.")
    try:
        scope = playbook_scope(conn, tenant_id, playbook)
    except ApiError:
        return [*failures, "No connector declares this playbook's action."], None, playbook
    if (scope.connector_id, scope.action_capability) != (request.connector_id,
                                                         request.action_capability):
        failures.append("The playbook's action changed after the request.")
    case_status = conn.execute(
        text("SELECT status FROM domain_cases WHERE id = :id FOR UPDATE"),
        {"id": request.case_id},
    ).scalar_one()
    if case_status != "ACTION_PENDING":
        failures.append(f"The case is {case_status}, not ACTION_PENDING.")
    verdict = evaluate(conn, request.case_id, playbook, request.parameters)
    if not verdict["eligible"]:
        failures.extend(verdict["failures"])
    elif verdict["target"] != request.target:
        failures.append("The target changed after approval.")
    key = execution_key(playbook, request.target)
    if key is None:
        failures.append("The target lacks the fields its idempotency key needs.")
    elif conn.execute(
        text("SELECT 1 FROM executions WHERE idempotency_key = :key"), {"key": key}
    ).first():
        failures.append("An execution already exists for this target.")
    return failures, scope, playbook


def in_shadow(conn: Connection, scope: ExactScope) -> bool:
    """True when the exact scope is SHADOW (POL-06) and so may not act."""
    return resolve_mode(conn, scope).mode != "LIVE"


def _write_decision(conn: Connection, ctx: TenantContext, request_id: UUID, parent: Any,
                    outcome: str, reason: str, conditions: dict[str, Any]) -> UUID:
    decision_id = uuid4()
    conn.execute(
        text(
            """
            INSERT INTO policy_decisions (id, tenant_id, request_id, parent_decision_id, stage,
                policy_version, approval_required, decision, conditions, reason)
            VALUES (:id, :tenant, :request, :parent, 'EXECUTION', :version, :required,
                :decision, CAST(:conditions AS jsonb), :reason)
            """
        ),
        {"id": decision_id, "tenant": ctx.tenant_id, "request": request_id, "parent": parent.id,
         "version": GATEWAY_VERSION, "required": parent.approval_required, "decision": outcome,
         "conditions": json.dumps(conditions), "reason": reason},
    )
    return decision_id


def execution_stage(ctx: TenantContext, request_id: UUID) -> UUID | None:
    """Evaluate one request for execution and write its EXECUTION-stage decision. Returns the
    execution ID when the decision is ALLOW, after its grant is issued."""
    try:
        execution_id = _execution_stage(ctx, request_id)
    except IntegrityError:
        # Another request reached the same target first; its execution stands.
        raise conflict("EXECUTION_EXISTS", "An execution already exists for this target.") \
            from None
    if execution_id is not None:
        issue_grant(ctx, execution_id)
    return execution_id


def _execution_stage(ctx: TenantContext, request_id: UUID) -> UUID | None:
    with tenant_tx(ctx) as conn:
        request = _locked_request(conn, request_id)
        if request.status not in EVALUABLE:
            return None
        parent = _request_decision(conn, request_id)
        approval = conn.execute(
            text(
                "SELECT id FROM playbook_approvals WHERE request_policy_decision_id = :d "
                "AND decision = 'APPROVED'"
            ),
            {"d": parent.id},
        ).one_or_none()
        if parent.approval_required and approval is None:
            return None
        failures, scope, playbook = _revalidate(conn, ctx.tenant_id, request)
        base = {"request_id": str(request_id), "playbook_id": _qualified(request),
                "request_policy_decision_id": str(parent.id),
                "approval_id": str(approval.id) if approval else None, **_scope_detail(scope)}
        if failures:
            decision_id = _write_decision(
                conn, ctx, request_id, parent, "DENY", "Execution-stage revalidation failed.",
                {"failures": failures, **_scope_detail(scope)})
            advance(conn, request_id, "DENIED")
            write_audit(conn, ctx.tenant_id, ctx.principal, "playbook.execution_denied",
                        "playbook_request",
                        {**base, "execution_policy_decision_id": str(decision_id),
                         "failures": failures},
                        decision="DENY", policy_rules=["POL-09", "POL-11"])
            return None
        if request.status != "PRECONDITIONS_VALIDATED":
            advance(conn, request_id, "PRECONDITIONS_VALIDATED")
        if in_shadow(conn, scope):
            decision_id = _write_decision(
                conn, ctx, request_id, parent, "POLICY_BLOCKED", "The exact scope is SHADOW.",
                {"policy": "POL-06", "check": "SHADOW", **_scope_detail(scope)})
            write_audit(conn, ctx.tenant_id, ctx.principal, "playbook.execution_blocked",
                        "playbook_request",
                        {**base, "execution_policy_decision_id": str(decision_id),
                         "policy": "POL-06", "check": "SHADOW"},
                        decision="POLICY_BLOCKED", policy_rules=["POL-06"])
            return None
        decision_id = _write_decision(
            conn, ctx, request_id, parent, "ALLOW",
            "Preconditions, target, connector action and mode hold.",
            {"preconditions": {"eligible": True, "failures": []}, **_scope_detail(scope)})
        advance(conn, request_id, "EXECUTION_POLICY_ALLOWED", "EXECUTION_CREATED")
        execution_id = uuid4()
        conn.execute(
            text(
                """
                INSERT INTO executions (id, tenant_id, request_id, request_policy_decision_id,
                    execution_policy_decision_id, approval_id, pack_namespace, pack_version,
                    playbook_id, playbook_version, connector_id, action_capability, target,
                    idempotency_key)
                VALUES (:id, :tenant, :request, :parent, :decision, :approval, :pack,
                    :pack_version, :playbook, :version, :connector, :capability,
                    CAST(:target AS jsonb), :key)
                """
            ),
            {"id": execution_id, "tenant": ctx.tenant_id, "request": request_id,
             "parent": parent.id, "decision": decision_id,
             "approval": approval.id if approval else None, "pack": request.pack_namespace,
             "pack_version": request.pack_version, "playbook": request.playbook_id,
             "version": request.playbook_version, "connector": request.connector_id,
             "capability": request.action_capability, "target": json.dumps(request.target),
             "key": execution_key(playbook, request.target)},
        )
        write_audit(conn, ctx.tenant_id, ctx.principal, "playbook.execution_allowed",
                    "execution",
                    {**base, "execution_policy_decision_id": str(decision_id),
                     "execution_id": str(execution_id)},
                    decision="ALLOW", policy_rules=["POL-06", "POL-09"])
    return execution_id


def issue_grant(ctx: TenantContext, execution_id: UUID) -> UUID | None:
    """The single-use grant for one CREATED execution (POL-09). The exact scope's mode is
    checked again here; SHADOW blocks the execution and issues nothing."""
    token = secrets.token_urlsafe(32)
    grant_id = uuid4()
    expires_at = datetime.now(UTC) + GRANT_TTL
    with tenant_tx(ctx) as conn:
        execution = conn.execute(
            text("SELECT * FROM executions WHERE id = :id AND status = 'CREATED' FOR UPDATE"),
            {"id": execution_id},
        ).one_or_none()
        if execution is None:
            return None
        scope = ExactScope(ctx.tenant_id, execution.pack_namespace, execution.pack_version,
                           execution.connector_id, execution.action_capability)
        if in_shadow(conn, scope):
            conn.execute(
                text(
                    "UPDATE executions SET status = 'POLICY_BLOCKED', reason = :reason, "
                    "updated_at = now() WHERE id = :id"
                ),
                {"id": execution_id, "reason": "POL-06 SHADOW at grant issue"},
            )
            write_audit(conn, ctx.tenant_id, ctx.principal, "execution.grant_blocked",
                        "execution",
                        {"execution_id": str(execution_id), "policy": "POL-06",
                         "check": "SHADOW"},
                        decision="POLICY_BLOCKED", policy_rules=["POL-06"])
            return None
        conn.execute(
            text(
                """
                INSERT INTO execution_grants (id, tenant_id, execution_id, request_id,
                    request_policy_decision_id, execution_policy_decision_id, approval_id,
                    playbook_version, connector_id, action_capability, target, idempotency_key,
                    permission_keys, token_hash, expires_at)
                SELECT :id, tenant_id, id, request_id, request_policy_decision_id,
                    execution_policy_decision_id, approval_id, playbook_version, connector_id,
                    action_capability, target, idempotency_key, :permissions, :hash, :expires
                FROM executions WHERE id = :execution
                """
            ),
            {"id": grant_id, "execution": execution_id, "permissions": GRANT_PERMISSIONS,
             "hash": hashlib.sha256(token.encode()).hexdigest(), "expires": expires_at},
        )
        conn.execute(
            text(
                "UPDATE executions SET status = 'GRANT_ISSUED', updated_at = now() "
                "WHERE id = :id"
            ),
            {"id": execution_id},
        )
        write_audit(conn, ctx.tenant_id, ctx.principal, "execution.grant_issued", "execution",
                    {"execution_id": str(execution_id), "grant_id": str(grant_id),
                     "expires_at": expires_at.isoformat(),
                     "idempotency_key": execution.idempotency_key},
                    decision="ALLOW", policy_rules=["POL-06", "POL-09"])
    dispatch(ExecutionDispatch(ctx.tenant_id, execution_id, grant_id, token, expires_at))
    return grant_id


def reevaluate_scope(ctx: TenantContext, domain_pack_id: str, connector_id: str,
                     action_capability: str) -> list[str]:
    """After one exact scope is switched to LIVE, evaluate again every request it blocked, and
    grant any execution left CREATED without one. Other scopes are untouched."""
    with tenant_tx(ctx) as conn:
        waiting = conn.execute(
            text(
                """
                SELECT id FROM playbook_requests
                WHERE status = 'PRECONDITIONS_VALIDATED' AND pack_namespace = :pack
                  AND connector_id = :connector AND action_capability = :capability
                ORDER BY created_at
                """
            ),
            {"pack": domain_pack_id, "connector": connector_id,
             "capability": action_capability},
        ).scalars().all()
        ungranted = conn.execute(
            text(
                """
                SELECT id FROM executions
                WHERE status = 'CREATED' AND pack_namespace = :pack
                  AND connector_id = :connector AND action_capability = :capability
                """
            ),
            {"pack": domain_pack_id, "connector": connector_id,
             "capability": action_capability},
        ).scalars().all()
    for execution_id in ungranted:
        issue_grant(ctx, execution_id)
    evaluated = []
    for request_id in waiting:
        try:
            execution_stage(ctx, request_id)
        except ApiError:
            logger.warning("request %s could not be evaluated after the LIVE switch", request_id)
        evaluated.append(str(request_id))
    return evaluated
