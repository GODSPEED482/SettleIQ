"""Write-only credential intake store (spec v1.7 §11.3, AC-PP-14, OPEN-PP-03).

A tenant's provider credential, submitted once through `PUT /api/sources/{id}/credential-value`,
is sealed with AES-256-GCM by the api before it reaches this table. The key never enters the
database, and the associated data binds each ciphertext to its tenant and source. One row per
source; rotation overwrites it and moving the source to another reference deletes it.

Revision ID: 0015
Revises: 0014
"""

from alembic import op

from app.migration_support import APP_ROLE, enable_tenant_rls

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE source_credential_values (
            tenant_id uuid NOT NULL REFERENCES tenants (id),
            source_id uuid NOT NULL,
            nonce bytea NOT NULL CHECK (octet_length(nonce) = 12),
            ciphertext bytea NOT NULL,
            key_fingerprint text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, source_id),
            FOREIGN KEY (tenant_id, source_id) REFERENCES data_sources (tenant_id, id)
                ON DELETE CASCADE
        )
        """
    )
    enable_tenant_rls("source_credential_values")
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON source_credential_values TO {APP_ROLE}"
    )


def downgrade() -> None:
    op.execute("DROP TABLE source_credential_values")
