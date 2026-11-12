"""The execution chain: request, decisions, approval, execution, grant, attempts (spec 6.3, 9).

A human request comes first, then its immutable REQUEST-stage decision, then an approval when
that decision requires one, then an immutable EXECUTION-stage decision, then the execution row,
then single-use grants and the append-only action attempts. Every table is tenant-owned with
forced RLS, and every reference is a (tenant_id, id) composite foreign key, so no row can point
into another tenant even though foreign-key checks bypass row-level security.

Triggers hold the rules a foreign key cannot see (AC-PLY-09, AC-PLY-11):
- an EXECUTION decision's parent is the REQUEST decision of the same request;
- an approval answers a REQUEST decision that requires one, and never by the requester (POL-01);
- an execution and a grant carry an approved approval if and only if the REQUEST decision
  required one, and reference an EXECUTION-stage ALLOW whose parent is that decision;
- requests and executions move only along the spec 9.3 state machines;
- a grant is consumed once, before it expires, and changes nothing else.

Tenant approval policy is data in `playbook_approval_policies`; an absent row means REQUIRED.

Revision ID: 0016
Revises: 0015
"""

from alembic import op

from app.migration_support import APP_ROLE, enable_tenant_rls

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

TENANT_TABLES = (
    "playbook_approval_policies",
    "playbook_requests",
    "policy_decisions",
    "playbook_approvals",
    "executions",
    "execution_grants",
    "execution_action_attempts",
)
APPEND_ONLY = (
    "playbook_approval_policies",
    "policy_decisions",
    "playbook_approvals",
    "execution_action_attempts",
)
GUARDED = ("playbook_requests", "executions", "execution_grants")

# Spec 9.3. A REQUEST- or EXECUTION-stage POLICY_BLOCKED leaves the request where it is, so the
# same approved request can be evaluated again once its exact scope is LIVE.
REQUEST_TRANSITIONS = (
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
)
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


def _states(transitions: tuple[tuple[str, str], ...]) -> str:
    states = sorted({state for pair in transitions for state in pair})
    return ", ".join(f"'{s}'" for s in states)


def _pairs(transitions: tuple[tuple[str, str], ...]) -> str:
    return ", ".join(f"('{a}', '{b}')" for a, b in transitions)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE playbook_approval_policies (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants (id),
            pack_namespace text NOT NULL,
            playbook_id text NOT NULL,
            version integer NOT NULL CHECK (version > 0),
            approval_policy text NOT NULL CHECK (approval_policy IN ('REQUIRED', 'NOT_REQUIRED')),
            reason text NOT NULL,
            set_by uuid NOT NULL REFERENCES users (id),
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, pack_namespace, playbook_id, version)
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE playbook_requests (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants (id),
            case_id uuid NOT NULL,
            pack_namespace text NOT NULL,
            pack_version text NOT NULL,
            playbook_id text NOT NULL,
            playbook_version text NOT NULL,
            connector_id text NOT NULL,
            action_capability text NOT NULL,
            requested_by uuid NOT NULL REFERENCES users (id),
            parameters jsonb NOT NULL,
            target jsonb NOT NULL,
            evidence_refs jsonb NOT NULL,
            status text NOT NULL DEFAULT 'CREATED'
                CHECK (status IN ({_states(REQUEST_TRANSITIONS)})),
            idempotency_key text NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 200),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, idempotency_key),
            FOREIGN KEY (tenant_id, case_id) REFERENCES domain_cases (tenant_id, id)
        )
        """
    )
    op.execute("CREATE INDEX playbook_requests_case_idx ON playbook_requests (tenant_id, case_id)")
    op.execute(
        """
        CREATE TABLE policy_decisions (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants (id),
            request_id uuid NOT NULL,
            parent_decision_id uuid,
            stage text NOT NULL CHECK (stage IN ('REQUEST', 'EXECUTION')),
            policy_version text NOT NULL,
            approval_required boolean NOT NULL,
            decision text NOT NULL,
            conditions jsonb NOT NULL,
            reason text NOT NULL,
            attestation_id text,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, id),
            CHECK (
                (stage = 'REQUEST' AND parent_decision_id IS NULL AND decision IN
                    ('DENY', 'NEEDS_APPROVAL', 'ALLOW_PENDING_PRECONDITIONS', 'POLICY_BLOCKED'))
                OR (stage = 'EXECUTION' AND parent_decision_id IS NOT NULL
                    AND decision IN ('DENY', 'ALLOW', 'POLICY_BLOCKED'))
            ),
            CHECK (decision <> 'NEEDS_APPROVAL' OR approval_required),
            CHECK (decision <> 'ALLOW_PENDING_PRECONDITIONS' OR NOT approval_required),
            FOREIGN KEY (tenant_id, request_id) REFERENCES playbook_requests (tenant_id, id),
            FOREIGN KEY (tenant_id, parent_decision_id) REFERENCES policy_decisions (tenant_id, id)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX policy_decisions_one_request_stage ON policy_decisions (request_id) "
        "WHERE stage = 'REQUEST'"
    )
    op.execute(
        """
        CREATE TABLE playbook_approvals (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants (id),
            request_id uuid NOT NULL,
            request_policy_decision_id uuid NOT NULL,
            approver_user_id uuid NOT NULL REFERENCES users (id),
            decision text NOT NULL CHECK (decision IN ('APPROVED', 'REJECTED')),
            reason text,
            policy_version text NOT NULL,
            conditions jsonb NOT NULL DEFAULT '{}',
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, request_id, request_policy_decision_id),
            FOREIGN KEY (tenant_id, request_id) REFERENCES playbook_requests (tenant_id, id),
            FOREIGN KEY (tenant_id, request_policy_decision_id)
                REFERENCES policy_decisions (tenant_id, id)
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE executions (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants (id),
            request_id uuid NOT NULL,
            request_policy_decision_id uuid NOT NULL,
            execution_policy_decision_id uuid NOT NULL,
            approval_id uuid,
            pack_namespace text NOT NULL,
            pack_version text NOT NULL,
            playbook_id text NOT NULL,
            playbook_version text NOT NULL,
            connector_id text NOT NULL,
            action_capability text NOT NULL,
            target jsonb NOT NULL,
            idempotency_key text NOT NULL,
            status text NOT NULL DEFAULT 'CREATED'
                CHECK (status IN ({_states(EXECUTION_TRANSITIONS)})),
            worker_principal_id uuid,
            started_at timestamptz,
            completed_at timestamptz,
            result_ref jsonb,
            reason text,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, request_id),
            UNIQUE (tenant_id, idempotency_key),
            FOREIGN KEY (tenant_id, request_id) REFERENCES playbook_requests (tenant_id, id),
            FOREIGN KEY (tenant_id, request_policy_decision_id)
                REFERENCES policy_decisions (tenant_id, id),
            FOREIGN KEY (tenant_id, execution_policy_decision_id)
                REFERENCES policy_decisions (tenant_id, id),
            FOREIGN KEY (tenant_id, approval_id) REFERENCES playbook_approvals (tenant_id, id),
            FOREIGN KEY (tenant_id, worker_principal_id)
                REFERENCES service_principals (tenant_id, id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE execution_grants (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants (id),
            execution_id uuid NOT NULL,
            request_id uuid NOT NULL,
            request_policy_decision_id uuid NOT NULL,
            execution_policy_decision_id uuid NOT NULL,
            approval_id uuid,
            playbook_version text NOT NULL,
            connector_id text NOT NULL,
            action_capability text NOT NULL,
            target jsonb NOT NULL,
            idempotency_key text NOT NULL,
            permission_keys text[] NOT NULL,
            token_hash text NOT NULL UNIQUE CHECK (token_hash ~ '^[0-9a-f]{64}$'),
            attestation_id text NOT NULL,
            issued_at timestamptz NOT NULL DEFAULT now(),
            expires_at timestamptz NOT NULL,
            consumed_at timestamptz,
            UNIQUE (tenant_id, id),
            CHECK (expires_at > issued_at),
            FOREIGN KEY (tenant_id, execution_id) REFERENCES executions (tenant_id, id),
            FOREIGN KEY (tenant_id, request_id) REFERENCES playbook_requests (tenant_id, id),
            FOREIGN KEY (tenant_id, request_policy_decision_id)
                REFERENCES policy_decisions (tenant_id, id),
            FOREIGN KEY (tenant_id, execution_policy_decision_id)
                REFERENCES policy_decisions (tenant_id, id),
            FOREIGN KEY (tenant_id, approval_id) REFERENCES playbook_approvals (tenant_id, id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE execution_action_attempts (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants (id),
            execution_id uuid NOT NULL,
            sequence integer NOT NULL CHECK (sequence > 0),
            connector_id text NOT NULL,
            action_capability text NOT NULL,
            target jsonb NOT NULL,
            idempotency_key text NOT NULL,
            request_evidence jsonb NOT NULL DEFAULT '{}',
            response_evidence jsonb,
            status text NOT NULL CHECK (status IN
                ('STARTED', 'SUCCEEDED', 'FAILED', 'TIMEOUT', 'OUTCOME_UNKNOWN', 'VERIFIED')),
            error_class text,
            started_at timestamptz NOT NULL DEFAULT now(),
            finished_at timestamptz,
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, execution_id, sequence),
            FOREIGN KEY (tenant_id, execution_id) REFERENCES executions (tenant_id, id)
        )
        """
    )

    op.execute(
        """
        CREATE FUNCTION guard_policy_decision_insert() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            parent policy_decisions%ROWTYPE;
        BEGIN
            IF NEW.stage = 'EXECUTION' THEN
                SELECT * INTO parent FROM policy_decisions WHERE id = NEW.parent_decision_id;
                IF parent.stage IS DISTINCT FROM 'REQUEST'
                   OR parent.request_id IS DISTINCT FROM NEW.request_id THEN
                    RAISE EXCEPTION 'an EXECUTION decision follows its own request decision';
                END IF;
                IF NEW.approval_required IS DISTINCT FROM parent.approval_required THEN
                    RAISE EXCEPTION 'an EXECUTION decision keeps the approval requirement';
                END IF;
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER policy_decisions_chain BEFORE INSERT ON policy_decisions
        FOR EACH ROW EXECUTE FUNCTION guard_policy_decision_insert()
        """
    )
    op.execute(
        """
        CREATE FUNCTION guard_playbook_approval_insert() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            decided policy_decisions%ROWTYPE;
            requester uuid;
        BEGIN
            SELECT * INTO decided FROM policy_decisions WHERE id = NEW.request_policy_decision_id;
            IF decided.stage IS DISTINCT FROM 'REQUEST'
               OR decided.request_id IS DISTINCT FROM NEW.request_id
               OR decided.decision IS DISTINCT FROM 'NEEDS_APPROVAL' THEN
                RAISE EXCEPTION 'an approval answers a REQUEST decision that needs approval';
            END IF;
            SELECT requested_by INTO requester FROM playbook_requests WHERE id = NEW.request_id;
            IF requester = NEW.approver_user_id THEN
                RAISE EXCEPTION 'POL-01: the requester cannot decide their own request';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER playbook_approvals_chain BEFORE INSERT ON playbook_approvals
        FOR EACH ROW EXECUTE FUNCTION guard_playbook_approval_insert()
        """
    )
    # Shared by executions and grants: the conditional approval relationship (spec 6.3 items
    # 3-6). Both tables name the same columns, so one function checks either.
    op.execute(
        """
        CREATE FUNCTION check_execution_authority(
            p_request_id uuid, p_request_decision uuid, p_execution_decision uuid,
            p_approval uuid
        ) RETURNS void
        LANGUAGE plpgsql AS $$
        DECLARE
            req policy_decisions%ROWTYPE;
            exe policy_decisions%ROWTYPE;
            approval playbook_approvals%ROWTYPE;
        BEGIN
            SELECT * INTO req FROM policy_decisions WHERE id = p_request_decision;
            SELECT * INTO exe FROM policy_decisions WHERE id = p_execution_decision;
            IF req.stage IS DISTINCT FROM 'REQUEST' OR req.request_id IS DISTINCT FROM p_request_id
            THEN
                RAISE EXCEPTION 'the request decision must be the REQUEST stage of this request';
            END IF;
            IF exe.stage IS DISTINCT FROM 'EXECUTION' OR exe.decision IS DISTINCT FROM 'ALLOW'
               OR exe.parent_decision_id IS DISTINCT FROM req.id THEN
                RAISE EXCEPTION 'execution needs an EXECUTION-stage ALLOW under the request decision';
            END IF;
            IF req.approval_required THEN
                SELECT * INTO approval FROM playbook_approvals WHERE id = p_approval;
                IF approval.id IS NULL OR approval.decision <> 'APPROVED'
                   OR approval.request_policy_decision_id <> req.id THEN
                    RAISE EXCEPTION 'approval is required and must approve this request decision';
                END IF;
            ELSIF p_approval IS NOT NULL THEN
                RAISE EXCEPTION 'approval_id is NULL when the request decision needs no approval';
            END IF;
        END
        $$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION guard_execution() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.status <> 'CREATED' THEN
                    RAISE EXCEPTION 'an execution starts CREATED';
                END IF;
                PERFORM check_execution_authority(NEW.request_id, NEW.request_policy_decision_id,
                    NEW.execution_policy_decision_id, NEW.approval_id);
                RETURN NEW;
            END IF;
            IF (NEW.tenant_id, NEW.request_id, NEW.request_policy_decision_id,
                NEW.execution_policy_decision_id, NEW.approval_id, NEW.pack_namespace,
                NEW.pack_version, NEW.playbook_id, NEW.playbook_version, NEW.connector_id,
                NEW.action_capability, NEW.target, NEW.idempotency_key, NEW.created_at)
               IS DISTINCT FROM
               (OLD.tenant_id, OLD.request_id, OLD.request_policy_decision_id,
                OLD.execution_policy_decision_id, OLD.approval_id, OLD.pack_namespace,
                OLD.pack_version, OLD.playbook_id, OLD.playbook_version, OLD.connector_id,
                OLD.action_capability, OLD.target, OLD.idempotency_key, OLD.created_at) THEN
                RAISE EXCEPTION 'an execution keeps its authority, action and target';
            END IF;
            IF NEW.status IS DISTINCT FROM OLD.status AND (OLD.status, NEW.status) NOT IN (
                {_pairs(EXECUTION_TRANSITIONS)}
            ) THEN
                RAISE EXCEPTION 'execution transition % -> % is not allowed',
                    OLD.status, NEW.status;
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER executions_guard BEFORE INSERT OR UPDATE ON executions
        FOR EACH ROW EXECUTE FUNCTION guard_execution()
        """
    )
    op.execute(
        """
        CREATE FUNCTION guard_execution_grant() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            ex executions%ROWTYPE;
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.consumed_at IS NOT NULL THEN
                    RAISE EXCEPTION 'a grant is issued unconsumed';
                END IF;
                SELECT * INTO ex FROM executions WHERE id = NEW.execution_id;
                IF (ex.request_id, ex.request_policy_decision_id, ex.execution_policy_decision_id,
                    ex.approval_id, ex.playbook_version, ex.connector_id, ex.action_capability,
                    ex.target, ex.idempotency_key)
                   IS DISTINCT FROM
                   (NEW.request_id, NEW.request_policy_decision_id,
                    NEW.execution_policy_decision_id, NEW.approval_id, NEW.playbook_version,
                    NEW.connector_id, NEW.action_capability, NEW.target, NEW.idempotency_key) THEN
                    RAISE EXCEPTION 'a grant is bound to exactly its execution';
                END IF;
                PERFORM check_execution_authority(NEW.request_id, NEW.request_policy_decision_id,
                    NEW.execution_policy_decision_id, NEW.approval_id);
                RETURN NEW;
            END IF;
            IF OLD.consumed_at IS NOT NULL THEN
                RAISE EXCEPTION 'a grant is consumed once';
            END IF;
            IF NEW.consumed_at IS NULL OR now() >= OLD.expires_at THEN
                RAISE EXCEPTION 'only an unexpired grant can be consumed';
            END IF;
            IF (NEW.tenant_id, NEW.execution_id, NEW.request_id, NEW.request_policy_decision_id,
                NEW.execution_policy_decision_id, NEW.approval_id, NEW.playbook_version,
                NEW.connector_id, NEW.action_capability, NEW.target, NEW.idempotency_key,
                NEW.permission_keys, NEW.token_hash, NEW.attestation_id, NEW.issued_at,
                NEW.expires_at)
               IS DISTINCT FROM
               (OLD.tenant_id, OLD.execution_id, OLD.request_id, OLD.request_policy_decision_id,
                OLD.execution_policy_decision_id, OLD.approval_id, OLD.playbook_version,
                OLD.connector_id, OLD.action_capability, OLD.target, OLD.idempotency_key,
                OLD.permission_keys, OLD.token_hash, OLD.attestation_id, OLD.issued_at,
                OLD.expires_at) THEN
                RAISE EXCEPTION 'consuming a grant changes nothing else';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER execution_grants_guard BEFORE INSERT OR UPDATE ON execution_grants
        FOR EACH ROW EXECUTE FUNCTION guard_execution_grant()
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION guard_playbook_request() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.status <> 'CREATED' THEN
                    RAISE EXCEPTION 'a playbook request starts CREATED';
                END IF;
                RETURN NEW;
            END IF;
            IF (NEW.tenant_id, NEW.case_id, NEW.pack_namespace, NEW.pack_version,
                NEW.playbook_id, NEW.playbook_version, NEW.connector_id, NEW.action_capability,
                NEW.requested_by, NEW.parameters, NEW.target, NEW.evidence_refs,
                NEW.idempotency_key, NEW.created_at)
               IS DISTINCT FROM
               (OLD.tenant_id, OLD.case_id, OLD.pack_namespace, OLD.pack_version,
                OLD.playbook_id, OLD.playbook_version, OLD.connector_id, OLD.action_capability,
                OLD.requested_by, OLD.parameters, OLD.target, OLD.evidence_refs,
                OLD.idempotency_key, OLD.created_at) THEN
                RAISE EXCEPTION 'a playbook request keeps its intent';
            END IF;
            IF NEW.status IS DISTINCT FROM OLD.status AND (OLD.status, NEW.status) NOT IN (
                {_pairs(REQUEST_TRANSITIONS)}
            ) THEN
                RAISE EXCEPTION 'request transition % -> % is not allowed',
                    OLD.status, NEW.status;
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER playbook_requests_guard BEFORE INSERT OR UPDATE ON playbook_requests
        FOR EACH ROW EXECUTE FUNCTION guard_playbook_request()
        """
    )

    for table in TENANT_TABLES:
        enable_tenant_rls(table)
    for table in APPEND_ONLY:
        op.execute(f"GRANT SELECT, INSERT ON {table} TO {APP_ROLE}")
    for table in GUARDED:
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON {table} TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP TRIGGER playbook_requests_guard ON playbook_requests")
    op.execute("DROP FUNCTION guard_playbook_request()")
    op.execute("DROP TRIGGER execution_grants_guard ON execution_grants")
    op.execute("DROP FUNCTION guard_execution_grant()")
    op.execute("DROP TRIGGER executions_guard ON executions")
    op.execute("DROP FUNCTION guard_execution()")
    op.execute("DROP FUNCTION check_execution_authority(uuid, uuid, uuid, uuid)")
    op.execute("DROP TRIGGER playbook_approvals_chain ON playbook_approvals")
    op.execute("DROP FUNCTION guard_playbook_approval_insert()")
    op.execute("DROP TRIGGER policy_decisions_chain ON policy_decisions")
    op.execute("DROP FUNCTION guard_policy_decision_insert()")
    for table in reversed(TENANT_TABLES):
        op.execute(f"DROP TABLE {table}")
