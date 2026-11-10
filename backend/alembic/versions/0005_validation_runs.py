"""Hash-bound ontology validation runs (POL-12, spec 5.7). Append-only for the application.

Revision ID: 0005
Revises: 0004
"""

from alembic import op

from app.migration_support import APP_ROLE, enable_tenant_rls

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None



def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE ontology_validation_runs (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants (id),
            draft_id uuid NOT NULL,
            draft_revision integer NOT NULL,
            content_hash text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
            passed boolean NOT NULL,
            results jsonb NOT NULL,
            validated_by uuid NOT NULL REFERENCES users (id),
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, id),
            FOREIGN KEY (tenant_id, draft_id) REFERENCES ontology_drafts (tenant_id, id)
        )
        """
    )
    enable_tenant_rls("ontology_validation_runs")
    op.execute(f"GRANT SELECT, INSERT ON ontology_validation_runs TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP TABLE ontology_validation_runs")
