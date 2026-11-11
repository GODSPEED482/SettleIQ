"""Webhook inbox and platform security ingress log (tasks 4.3, 4.4, spec 6.3, 10.4, POL-15).

Webhook ingress verifies signatures and records forensics before tenant context is
established. Successfully verified events are durably staged in `webhook_inbox`, which is
tenant-owned and RLS-protected, and deduplicated on `(tenant_id, source_id, provider_event_id)`.
Asynchronous processing claims rows with `FOR UPDATE SKIP LOCKED`.

Revision ID: 0012
Revises: 0011
"""

from alembic import op

from app.migration_support import APP_ROLE, enable_tenant_rls

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

TENANT_TABLES = ("webhook_inbox",)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE webhook_inbox (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants (id),
            source_id uuid NOT NULL,
            provider_event_id text NOT NULL,
            event_type text NOT NULL,
            raw_evidence_id uuid NOT NULL,
            status text NOT NULL DEFAULT 'RECEIVED'
                CHECK (status IN ('RECEIVED', 'PROCESSING', 'PROCESSED', 'FAILED', 'DEAD_LETTER')),
            attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
            max_attempts integer NOT NULL DEFAULT 3 CHECK (max_attempts >= 1),
            last_error text,
            provider_timestamp timestamptz,
            received_at timestamptz NOT NULL DEFAULT now(),
            processed_at timestamptz,
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, source_id, provider_event_id),
            FOREIGN KEY (tenant_id, source_id) REFERENCES data_sources (tenant_id, id),
            FOREIGN KEY (tenant_id, raw_evidence_id) REFERENCES raw_evidence (tenant_id, id)
        )
        """
    )
    op.execute(
        "CREATE INDEX webhook_inbox_queue_idx ON webhook_inbox (status, received_at)"
    )
    op.execute(
        "CREATE INDEX webhook_inbox_source_idx "
        "ON webhook_inbox (tenant_id, source_id, received_at)"
    )

    for table in TENANT_TABLES:
        enable_tenant_rls(table)

    op.execute(f"GRANT SELECT, INSERT, UPDATE ON webhook_inbox TO {APP_ROLE}")

    # Platform security ingress log: not tenant-owned, no RLS. Forensics only for bad
    # signatures or unknown sources before tenant derivation (spec 10.4, AC-PP-03).
    op.execute(
        """
        CREATE TABLE platform_security_ingress_logs (
            id uuid PRIMARY KEY,
            source_id text NOT NULL,
            reason text NOT NULL,
            remote_ip text,
            headers jsonb NOT NULL DEFAULT '{}',
            body_hash text,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX platform_security_ingress_source_idx "
        "ON platform_security_ingress_logs (source_id, created_at)"
    )
    op.execute(
        f"GRANT SELECT, INSERT ON platform_security_ingress_logs TO {APP_ROLE}"
    )


def downgrade() -> None:
    op.execute("DROP TABLE platform_security_ingress_logs")
    op.execute("DROP TABLE webhook_inbox")

