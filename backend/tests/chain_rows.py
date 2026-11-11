"""One execution chain for a tenant, written directly as the runtime role (task 5.1).

The schema tests need real rows to constrain. Each row is the smallest the constraints allow;
the gateway tests build chains through the routes instead.
"""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import text

from app.db import scoped_tx
from tests.domain_rows import seed_domain_rows

TARGET = json.dumps({"payment_id": "PAY-1"})


def insert_request(conn, tenant: UUID, case_id: UUID, requester: UUID, key: str) -> UUID:
    request_id = uuid4()
    conn.execute(
        text(
            """
            INSERT INTO playbook_requests (id, tenant_id, case_id, pack_namespace, pack_version,
                playbook_id, playbook_version, connector_id, action_capability, requested_by,
                parameters, target, evidence_refs, idempotency_key)
            VALUES (:id, :t, :case, 'finops', '0.2.0', 'PB-001', '1.0.0', 'internal-merchant',
                'merchant.payment_status.correct', :user, '{}', :target, '[]', :key)
            """
        ),
        {"id": request_id, "t": tenant, "case": case_id, "user": requester, "target": TARGET,
         "key": key},
    )
    return request_id


def insert_decision(conn, tenant: UUID, request_id: UUID, stage: str, decision: str, *,
                    approval_required: bool, parent: UUID | None = None) -> UUID:
    decision_id = uuid4()
    conn.execute(
        text(
            """
            INSERT INTO policy_decisions (id, tenant_id, request_id, parent_decision_id, stage,
                policy_version, approval_required, decision, conditions, reason)
            VALUES (:id, :t, :request, :parent, :stage, '1', :required, :decision, '{}', 'test')
            """
        ),
        {"id": decision_id, "t": tenant, "request": request_id, "parent": parent,
         "stage": stage, "required": approval_required, "decision": decision},
    )
    return decision_id


def insert_approval(conn, tenant: UUID, request_id: UUID, decision_id: UUID, approver: UUID,
                    decision: str = "APPROVED") -> UUID:
    approval_id = uuid4()
    conn.execute(
        text(
            """
            INSERT INTO playbook_approvals (id, tenant_id, request_id,
                request_policy_decision_id, approver_user_id, decision, policy_version)
            VALUES (:id, :t, :request, :decision_id, :approver, :decision, '1')
            """
        ),
        {"id": approval_id, "t": tenant, "request": request_id, "decision_id": decision_id,
         "approver": approver, "decision": decision},
    )
    return approval_id


def insert_execution(conn, tenant: UUID, request_id: UUID, request_decision: UUID,
                     execution_decision: UUID, approval_id: UUID | None, key: str) -> UUID:
    execution_id = uuid4()
    conn.execute(
        text(
            """
            INSERT INTO executions (id, tenant_id, request_id, request_policy_decision_id,
                execution_policy_decision_id, approval_id, pack_namespace, pack_version,
                playbook_id, playbook_version, connector_id, action_capability, target,
                idempotency_key)
            VALUES (:id, :t, :request, :req_decision, :exe_decision, :approval, 'finops',
                '0.2.0', 'PB-001', '1.0.0', 'internal-merchant',
                'merchant.payment_status.correct', :target, :key)
            """
        ),
        {"id": execution_id, "t": tenant, "request": request_id, "req_decision": request_decision,
         "exe_decision": execution_decision, "approval": approval_id, "target": TARGET,
         "key": key},
    )
    return execution_id


def insert_grant(conn, tenant: UUID, chain: dict, *, target: str = TARGET,
                 expires_in: timedelta = timedelta(minutes=5)) -> UUID:
    grant_id = uuid4()
    conn.execute(
        text(
            """
            INSERT INTO execution_grants (id, tenant_id, execution_id, request_id,
                request_policy_decision_id, execution_policy_decision_id, approval_id,
                playbook_version, connector_id, action_capability, target, idempotency_key,
                permission_keys, token_hash, attestation_id, expires_at)
            VALUES (:id, :t, :execution, :request, :req_decision, :exe_decision, :approval,
                '1.0.0', 'internal-merchant', 'merchant.payment_status.correct', :target, :key,
                ARRAY['playbook.execute'], :hash, 'att-1', :expires)
            """
        ),
        {"id": grant_id, "t": tenant, "execution": chain["execution"],
         "request": chain["request"], "req_decision": chain["request_decision"],
         "exe_decision": chain["execution_decision"], "approval": chain["approval"],
         "target": target, "key": chain["key"],
         "hash": hashlib.sha256(grant_id.bytes).hexdigest(),
         "expires": datetime.now(UTC) + expires_in},
    )
    return grant_id


def seed_chain(tenant: UUID, requester: UUID, approver: UUID, *,
               approval_required: bool = True) -> dict:
    """A request through to an issued grant, in one tenant, with or without approval."""
    case_id = seed_domain_rows(tenant, requester)["domain_cases"]
    key = f"req-{uuid4()}"
    with scoped_tx(tenant) as conn:
        request_id = insert_request(conn, tenant, case_id, requester, key)
        first = "NEEDS_APPROVAL" if approval_required else "ALLOW_PENDING_PRECONDITIONS"
        request_decision = insert_decision(conn, tenant, request_id, "REQUEST", first,
                                           approval_required=approval_required)
        approval_id = (insert_approval(conn, tenant, request_id, request_decision, approver)
                       if approval_required else None)
        execution_decision = insert_decision(conn, tenant, request_id, "EXECUTION", "ALLOW",
                                             approval_required=approval_required,
                                             parent=request_decision)
        execution_id = insert_execution(conn, tenant, request_id, request_decision,
                                        execution_decision, approval_id, key)
        chain = {"case": case_id, "request": request_id, "request_decision": request_decision,
                 "approval": approval_id, "execution_decision": execution_decision,
                 "execution": execution_id, "key": key}
        chain["grant"] = insert_grant(conn, tenant, chain)
    return chain
