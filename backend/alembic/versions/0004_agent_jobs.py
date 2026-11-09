"""Durable agent job queue and the dispatcher role (spec 8, NFR-011, NFR-023).

The agent holds no database credentials, so the dispatcher consumes this queue for it. Its only
cross-tenant access is claiming job rows; all other work runs under `scoped_tx(job.tenant_id)`.

Revision ID: 0004
Revises: 0003
"""

import os

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

APP_ROLE = "settleiq_app"
DISPATCHER_ROLE = "settleiq_dispatcher"
TENANT_PREDICATE = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    password = os.environ["DISPATCHER_DB_PASSWORD"]
    if not password:
        raise RuntimeError("DISPATCHER_DB_PASSWORD must not be empty")
    escaped = password.replace("'", "''")
    op.execute(
        f"""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{DISPATCHER_ROLE}') THEN
                CREATE ROLE {DISPATCHER_ROLE} LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD '{escaped}';
            END IF;
        END $$
        """
    )
    op.execute(
        """
        CREATE TABLE agent_jobs (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants (id),
            kind text NOT NULL CHECK (kind IN ('ONTOLOGY_DRAFT', 'INVESTIGATION')),
            subject_id uuid NOT NULL,
            requested_by uuid NOT NULL REFERENCES users (id),
            payload jsonb NOT NULL,
            status text NOT NULL DEFAULT 'QUEUED'
                CHECK (status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED')),
            error text,
            created_at timestamptz NOT NULL DEFAULT now(),
            started_at timestamptz,
            finished_at timestamptz,
            lease_expires_at timestamptz
        )
        """
    )
    op.execute("CREATE INDEX agent_jobs_queue_idx ON agent_jobs (status, created_at)")
    op.execute(
        "CREATE UNIQUE INDEX agent_jobs_one_in_flight ON agent_jobs (tenant_id, kind, subject_id) "
        "WHERE status IN ('QUEUED', 'RUNNING')"
    )
    op.execute("ALTER TABLE agent_jobs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE agent_jobs FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON agent_jobs "
        f"USING (tenant_id = {TENANT_PREDICATE}) "
        f"WITH CHECK (tenant_id = {TENANT_PREDICATE})"
    )
    op.execute(
        f"CREATE POLICY dispatcher_claim ON agent_jobs TO {DISPATCHER_ROLE} "
        "USING (true) WITH CHECK (true)"
    )

    # Model output waits here for review. It is never part of the candidate that gets published.
    op.execute("ALTER TABLE ontology_drafts ADD COLUMN proposals jsonb NOT NULL DEFAULT '[]'")

    op.execute(f"GRANT SELECT, INSERT ON agent_jobs TO {APP_ROLE}")
    op.execute(f"GRANT USAGE ON SCHEMA public TO {DISPATCHER_ROLE}")
    op.execute(f"GRANT SELECT, UPDATE ON agent_jobs TO {DISPATCHER_ROLE}")
    op.execute(f"GRANT SELECT, UPDATE (proposals, updated_at) ON ontology_drafts TO {DISPATCHER_ROLE}")
    op.execute(f"GRANT SELECT ON service_principals TO {DISPATCHER_ROLE}")
    op.execute(f"GRANT INSERT ON audit_logs TO {DISPATCHER_ROLE}")


def downgrade() -> None:
    op.execute("DROP TABLE agent_jobs")
    op.execute("ALTER TABLE ontology_drafts DROP COLUMN proposals")
    op.execute(f"REVOKE ALL ON ontology_drafts, service_principals, audit_logs FROM {DISPATCHER_ROLE}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {DISPATCHER_ROLE}")
    op.execute(f"DROP ROLE {DISPATCHER_ROLE}")
