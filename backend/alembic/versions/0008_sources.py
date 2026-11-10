"""Source registry, raw evidence and canonical source records (spec 6.3, 7.1, 10.4).

Every source table is tenant-owned and RLS-protected. Raw evidence and source records are
immutable for the application (SELECT, INSERT only). The trusted webhook lookup is a narrow
SECURITY DEFINER function: it returns a tenant ID for one source and nothing else, so a
webhook can derive tenant context only after its signature is verified.

Revision ID: 0008
Revises: 0007
"""

from alembic import op

from app.migration_support import APP_ROLE, enable_tenant_rls

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

TENANT_TABLES = ("data_sources", "source_objects", "raw_evidence", "source_records")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE data_sources (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants (id),
            connector_id text NOT NULL,
            connector_version text NOT NULL,
            source_type text NOT NULL,
            protocol text NOT NULL,
            display_name text NOT NULL,
            config jsonb NOT NULL DEFAULT '{}',
            secret_reference text,
            capabilities jsonb NOT NULL DEFAULT '[]',
            status text NOT NULL DEFAULT 'ACTIVE'
                CHECK (status IN ('ACTIVE', 'DISABLED', 'ERROR')),
            created_by uuid NOT NULL REFERENCES users (id),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, id)
        )
        """
    )
    op.execute("CREATE INDEX data_sources_tenant_idx ON data_sources (tenant_id, created_at)")
    op.execute(
        """
        CREATE TABLE source_objects (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants (id),
            source_id uuid NOT NULL,
            object_name text NOT NULL,
            object_type text NOT NULL
                CHECK (object_type IN ('TABLE', 'VIEW', 'CSV', 'API', 'EVENT')),
            schema_snapshot jsonb NOT NULL DEFAULT '{}',
            profile jsonb NOT NULL DEFAULT '{}',
            checksum text,
            discovered_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, source_id, object_name),
            FOREIGN KEY (tenant_id, source_id) REFERENCES data_sources (tenant_id, id)
        )
        """
    )
    op.execute(
        "CREATE INDEX source_objects_source_idx ON source_objects (tenant_id, source_id)"
    )
    op.execute(
        """
        CREATE TABLE raw_evidence (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants (id),
            source_id uuid NOT NULL,
            evidence_type text NOT NULL
                CHECK (evidence_type IN ('WEBHOOK', 'REPORT', 'RECORD', 'FILE')),
            storage_key text NOT NULL,
            content_hash text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
            byte_size integer NOT NULL CHECK (byte_size >= 0),
            content_type text NOT NULL,
            provider_event_id text,
            retrieval_context jsonb NOT NULL DEFAULT '{}',
            source_timestamp timestamptz,
            received_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, source_id, content_hash),
            FOREIGN KEY (tenant_id, source_id) REFERENCES data_sources (tenant_id, id)
        )
        """
    )
    op.execute(
        "CREATE INDEX raw_evidence_source_idx ON raw_evidence (tenant_id, source_id, received_at)"
    )
    op.execute(
        """
        CREATE TABLE source_records (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants (id),
            source_id uuid NOT NULL,
            source_object_id uuid NOT NULL,
            external_id text,
            record_type text NOT NULL
                CHECK (record_type ~ '^[A-Za-z][\\w.-]*:[\\w./#-]+$'),
            attributes jsonb NOT NULL,
            raw_evidence_id uuid NOT NULL,
            occurred_at timestamptz,
            observed_at timestamptz NOT NULL,
            payload_hash text NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
            connector_version text NOT NULL,
            mapping_version text,
            ingested_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, source_id, source_object_id, external_id),
            FOREIGN KEY (tenant_id, source_id) REFERENCES data_sources (tenant_id, id),
            FOREIGN KEY (tenant_id, source_object_id) REFERENCES source_objects (tenant_id, id),
            FOREIGN KEY (tenant_id, raw_evidence_id) REFERENCES raw_evidence (tenant_id, id)
        )
        """
    )
    op.execute(
        "CREATE INDEX source_records_lookup_idx "
        "ON source_records (tenant_id, source_id, record_type)"
    )

    for table in TENANT_TABLES:
        enable_tenant_rls(table)

    op.execute(f"GRANT SELECT, INSERT, UPDATE ON data_sources TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON source_objects TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON raw_evidence TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON source_records TO {APP_ROLE}")

    # Bind mappings and authority assertions to the source registry once it exists. NOT VALID
    # avoids failing on rows written before this migration; new rows are still checked.
    for table in ("source_mappings", "authority_assertions"):
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {table}_source_fk "
            f"FOREIGN KEY (tenant_id, source_id) REFERENCES data_sources (tenant_id, id) NOT VALID"
        )

    op.execute(
        """
        CREATE FUNCTION resolve_webhook_source(p_source_id uuid)
        RETURNS TABLE (tenant_id uuid, connector_id text, config jsonb)
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
            SELECT s.tenant_id, s.connector_id, s.config
            FROM public.data_sources s
            WHERE s.id = p_source_id AND s.status = 'ACTIVE'
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION resolve_webhook_source(uuid) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION resolve_webhook_source(uuid) TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP FUNCTION resolve_webhook_source(uuid)")
    for table in ("source_mappings", "authority_assertions"):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {table}_source_fk")
    for table in ("source_records", "raw_evidence", "source_objects", "data_sources"):
        op.execute(f"DROP TABLE {table}")
