"""Investigations and their tool trace (spec 6.3, 8.1, AC-INV-01).

An investigation is written RUNNING, with its job, by the human who starts it. The dispatcher
makes the single terminal write, to SUCCEEDED with a validated result or to FAILED with a
reason, and a trigger freezes the row from then on. Tool calls are append-only and can only be
added while their investigation runs, so a trace is bounded by its run.

The Agent never writes here directly: the tool API appends trace rows as the runtime role, and
the dispatcher role may update only the terminal columns of an investigation.

Revision ID: 0017
Revises: 0016
"""

from alembic import op

from app.migration_support import APP_ROLE, enable_tenant_rls

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

DISPATCHER_ROLE = "settleiq_dispatcher"
TERMINAL_COLUMNS = (
    "status", "model", "prompt_version", "result", "proposed_classification",
    "recommended_playbook", "confidence", "error", "finished_at",
)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE investigations (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants (id),
            case_id uuid NOT NULL,
            pack_namespace text NOT NULL,
            invoked_by uuid NOT NULL REFERENCES users (id),
            agent_principal_id uuid NOT NULL,
            job_id uuid NOT NULL REFERENCES agent_jobs (id),
            status text NOT NULL DEFAULT 'RUNNING'
                CHECK (status IN ('RUNNING', 'SUCCEEDED', 'FAILED')),
            tool_registry_version text NOT NULL CHECK (tool_registry_version ~ '^[0-9a-f]{64}$'),
            input_evidence jsonb NOT NULL,
            model text CHECK (length(model) <= 200),
            prompt_version text CHECK (length(prompt_version) <= 200),
            result jsonb,
            proposed_classification text,
            recommended_playbook text,
            confidence text CHECK (confidence IN ('low', 'medium', 'high')),
            error text,
            created_at timestamptz NOT NULL DEFAULT now(),
            finished_at timestamptz,
            UNIQUE (tenant_id, id),
            CHECK (status <> 'SUCCEEDED' OR (result IS NOT NULL AND error IS NULL
                AND model IS NOT NULL AND finished_at IS NOT NULL)),
            CHECK (status <> 'FAILED' OR (result IS NULL AND error IS NOT NULL
                AND finished_at IS NOT NULL)),
            FOREIGN KEY (tenant_id, case_id) REFERENCES domain_cases (tenant_id, id),
            FOREIGN KEY (tenant_id, agent_principal_id)
                REFERENCES service_principals (tenant_id, id)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX investigations_one_running ON investigations (tenant_id, case_id) "
        "WHERE status = 'RUNNING'"
    )
    op.execute(
        """
        CREATE TABLE investigation_tool_calls (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants (id),
            investigation_id uuid NOT NULL,
            tool_name text NOT NULL,
            tool_version text NOT NULL,
            permission text NOT NULL,
            agent_principal_id uuid NOT NULL,
            invoker_id uuid NOT NULL REFERENCES users (id),
            input_hash text NOT NULL CHECK (input_hash ~ '^[0-9a-f]{64}$'),
            input jsonb NOT NULL,
            output_hash text CHECK (output_hash ~ '^[0-9a-f]{64}$'),
            output jsonb,
            evidence_ids uuid[] NOT NULL DEFAULT '{}',
            outcome text NOT NULL CHECK (outcome IN ('OK', 'DENIED', 'ERROR')),
            error text,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            FOREIGN KEY (tenant_id, investigation_id) REFERENCES investigations (tenant_id, id),
            FOREIGN KEY (tenant_id, agent_principal_id)
                REFERENCES service_principals (tenant_id, id)
        )
        """
    )
    op.execute(
        "CREATE INDEX investigation_tool_calls_run_idx "
        "ON investigation_tool_calls (tenant_id, investigation_id, created_at)"
    )
    op.execute(
        """
        CREATE FUNCTION guard_investigation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.status <> 'RUNNING' OR NEW.result IS NOT NULL THEN
                    RAISE EXCEPTION 'an investigation starts RUNNING, with no result';
                END IF;
                RETURN NEW;
            END IF;
            IF OLD.status <> 'RUNNING' THEN
                RAISE EXCEPTION 'a finished investigation is immutable';
            END IF;
            IF NEW.status = 'RUNNING' THEN
                RAISE EXCEPTION 'an investigation is written once, at its terminal state';
            END IF;
            IF (NEW.id, NEW.tenant_id, NEW.case_id, NEW.pack_namespace, NEW.invoked_by,
                NEW.agent_principal_id, NEW.job_id, NEW.tool_registry_version,
                NEW.input_evidence, NEW.created_at)
               IS DISTINCT FROM
               (OLD.id, OLD.tenant_id, OLD.case_id, OLD.pack_namespace, OLD.invoked_by,
                OLD.agent_principal_id, OLD.job_id, OLD.tool_registry_version,
                OLD.input_evidence, OLD.created_at) THEN
                RAISE EXCEPTION 'an investigation keeps who, what and with which tools';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER investigations_guard BEFORE INSERT OR UPDATE ON investigations
        FOR EACH ROW EXECUTE FUNCTION guard_investigation()
        """
    )
    op.execute(
        """
        CREATE FUNCTION guard_investigation_tool_call() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            run investigations%ROWTYPE;
        BEGIN
            SELECT * INTO run FROM investigations WHERE id = NEW.investigation_id;
            IF run.status IS DISTINCT FROM 'RUNNING' THEN
                RAISE EXCEPTION 'tool calls are recorded only while the investigation runs';
            END IF;
            IF (run.agent_principal_id, run.invoked_by)
               IS DISTINCT FROM (NEW.agent_principal_id, NEW.invoker_id) THEN
                RAISE EXCEPTION 'a tool call is made by its investigation''s agent and invoker';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER investigation_tool_calls_guard BEFORE INSERT ON investigation_tool_calls
        FOR EACH ROW EXECUTE FUNCTION guard_investigation_tool_call()
        """
    )

    for table in ("investigations", "investigation_tool_calls"):
        enable_tenant_rls(table)
        op.execute(f"GRANT SELECT, INSERT ON {table} TO {APP_ROLE}")
        op.execute(f"GRANT SELECT ON {table} TO {DISPATCHER_ROLE}")
    columns = ", ".join(TERMINAL_COLUMNS)
    op.execute(f"GRANT UPDATE ({columns}) ON investigations TO {DISPATCHER_ROLE}")


def downgrade() -> None:
    op.execute("DROP TABLE investigation_tool_calls")
    op.execute("DROP FUNCTION guard_investigation_tool_call()")
    op.execute("DROP TABLE investigations")
    op.execute("DROP FUNCTION guard_investigation()")
