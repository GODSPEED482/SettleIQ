"""Domain packs and ontology composition (spec 5.1, 6.3).

Core, pack and tenant-extension content stay separate. Artifacts and published ontologies are
SELECT, INSERT only for the application; drafts are the only editable form.

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

APP_ROLE = "settleiq_app"
TENANT_PREDICATE = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"
TENANT_TABLES = (
    "tenant_domain_packs",
    "ontology_artifacts",
    "ontology_drafts",
    "ontologies",
    "ontology_relationships",
)


def enable_tenant_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} "
        f"USING (tenant_id = {TENANT_PREDICATE}) "
        f"WITH CHECK (tenant_id = {TENANT_PREDICATE})"
    )


def upgrade() -> None:
    # The installed pack catalogue is global and written only by app/authz/seed.py.
    op.execute(
        """
        CREATE TABLE domain_packs (
            namespace text NOT NULL,
            version text NOT NULL,
            manifest jsonb NOT NULL,
            PRIMARY KEY (namespace, version)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE tenant_domain_packs (
            tenant_id uuid NOT NULL REFERENCES tenants (id),
            namespace text NOT NULL,
            version text NOT NULL,
            status text NOT NULL CHECK (status IN ('ACTIVE', 'DISABLED')),
            changed_by uuid NOT NULL REFERENCES users (id),
            changed_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, namespace),
            FOREIGN KEY (namespace, version) REFERENCES domain_packs (namespace, version)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE ontology_artifacts (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants (id),
            layer text NOT NULL CHECK (layer IN ('CORE', 'PACK')),
            namespace text NOT NULL,
            version text NOT NULL,
            content jsonb NOT NULL,
            content_hash text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, layer, namespace, content_hash)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE ontology_drafts (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants (id),
            base_version integer NOT NULL,
            revision integer NOT NULL DEFAULT 1,
            status text NOT NULL DEFAULT 'OPEN'
                CHECK (status IN ('OPEN', 'PUBLISHED', 'ABANDONED')),
            composition jsonb NOT NULL,
            tenant_extension jsonb NOT NULL,
            created_by uuid NOT NULL REFERENCES users (id),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, id)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX ontology_drafts_one_open ON ontology_drafts (tenant_id) "
        "WHERE status = 'OPEN'"
    )
    op.execute(
        """
        CREATE TABLE ontologies (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants (id),
            version integer NOT NULL CHECK (version > 0),
            composition jsonb NOT NULL,
            tenant_extension jsonb NOT NULL,
            content_hash text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
            draft_id uuid NOT NULL,
            published_by uuid NOT NULL REFERENCES users (id),
            published_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, version),
            UNIQUE (tenant_id, id),
            FOREIGN KEY (tenant_id, draft_id) REFERENCES ontology_drafts (tenant_id, id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE ontology_relationships (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            ontology_id uuid NOT NULL,
            iri text NOT NULL,
            domain_iri text NOT NULL,
            range_iri text NOT NULL,
            definition jsonb NOT NULL DEFAULT '{}',
            UNIQUE (ontology_id, iri),
            FOREIGN KEY (tenant_id, ontology_id) REFERENCES ontologies (tenant_id, id)
        )
        """
    )

    for table in TENANT_TABLES:
        enable_tenant_rls(table)

    op.execute(f"GRANT SELECT ON domain_packs TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON tenant_domain_packs TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON ontology_drafts TO {APP_ROLE}")
    for table in ("ontology_artifacts", "ontologies", "ontology_relationships"):
        op.execute(f"GRANT SELECT, INSERT ON {table} TO {APP_ROLE}")


def downgrade() -> None:
    for table in (
        "ontology_relationships",
        "ontologies",
        "ontology_drafts",
        "ontology_artifacts",
        "tenant_domain_packs",
        "domain_packs",
    ):
        op.execute(f"DROP TABLE {table}")
