"""Versioned source mappings and fact-specific authority assertions (spec 5.4, 5.6).

Separate objects, as ARCH-07 requires. Both are append-only: a change adds a new version, and
retiring adds a RETIRED version, so history is never rewritten.

Revision ID: 0006
Revises: 0005
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

APP_ROLE = "settleiq_app"
TENANT_PREDICATE = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"
TABLES = ("source_mappings", "authority_assertions")


# TODO(G2 1.7 Rahul): add a foreign key from source_id to data_sources once that table exists.
def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE source_mappings (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants (id),
            draft_id uuid NOT NULL,
            source_id uuid NOT NULL,
            external_object text NOT NULL,
            field_path text NOT NULL,
            property_iri text NOT NULL,
            class_iri text NOT NULL,
            version integer NOT NULL CHECK (version > 0),
            status text NOT NULL CHECK (status IN ('ACTIVE', 'RETIRED')),
            datatype text NOT NULL,
            transform jsonb NOT NULL,
            nullable boolean NOT NULL,
            is_unique boolean NOT NULL,
            sync_strategy text NOT NULL,
            value_basis text NOT NULL,
            unit text,
            currency_field text,
            temporal_context text NOT NULL,
            method text NOT NULL,
            created_by uuid NOT NULL REFERENCES users (id),
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, draft_id, source_id, external_object, field_path, property_iri,
                version),
            FOREIGN KEY (tenant_id, draft_id) REFERENCES ontology_drafts (tenant_id, id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE authority_assertions (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants (id),
            draft_id uuid NOT NULL,
            property_iri text NOT NULL,
            entity_type_iri text NOT NULL,
            source_id uuid NOT NULL,
            version integer NOT NULL CHECK (version > 0),
            status text NOT NULL CHECK (status IN ('ACTIVE', 'RETIRED')),
            valid_from timestamptz NOT NULL,
            valid_to timestamptz,
            verification_method text NOT NULL,
            created_by uuid NOT NULL REFERENCES users (id),
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, draft_id, property_iri, entity_type_iri, source_id, version),
            CHECK (valid_to IS NULL OR valid_to > valid_from),
            FOREIGN KEY (tenant_id, draft_id) REFERENCES ontology_drafts (tenant_id, id)
        )
        """
    )
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (tenant_id = {TENANT_PREDICATE}) "
            f"WITH CHECK (tenant_id = {TENANT_PREDICATE})"
        )
        op.execute(f"GRANT SELECT, INSERT ON {table} TO {APP_ROLE}")


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE {table}")
