"""Domain runs requested by the tenant's Worker (task 4.4, spec 10.4, AC-PP-05).

A verified provider event triggers reconciliation, so the webhook processor starts a domain
run with no human behind it. `requested_by` named only users, so that insert failed its foreign
key and the run never happened. A run now names exactly one requester: a user, or one of the
tenant's own service principals.

Revision ID: 0013
Revises: 0012
"""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE domain_runs
            ALTER COLUMN requested_by DROP NOT NULL,
            ADD COLUMN requested_by_service uuid,
            ADD FOREIGN KEY (tenant_id, requested_by_service)
                REFERENCES service_principals (tenant_id, id),
            ADD CONSTRAINT domain_runs_one_requester
                CHECK (num_nonnulls(requested_by, requested_by_service) = 1)
        """
    )


def downgrade() -> None:
    # Fails while any Worker-requested run exists; their findings and cases depend on them.
    op.execute(
        """
        ALTER TABLE domain_runs
            DROP CONSTRAINT domain_runs_one_requester,
            DROP COLUMN requested_by_service,
            ALTER COLUMN requested_by SET NOT NULL
        """
    )
