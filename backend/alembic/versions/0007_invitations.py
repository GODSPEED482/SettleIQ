"""Invitation delivery state and the token lookup (spec 3.1, AC-IAM-09).

Acceptance has no tenant in its URL and RLS hides invitations until one is set, so
`invitation_tenant_by_token_hash` is the narrow trusted path: it returns only a tenant ID, and
only to a caller already holding the token.

Revision ID: 0007
Revises: 0006
"""

from alembic import op

from app.migration_support import APP_ROLE

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE tenant_invitations
            ADD COLUMN delivery_status text NOT NULL DEFAULT 'PENDING'
                CHECK (delivery_status IN ('PENDING', 'SENT', 'FAILED')),
            ADD COLUMN delivery_attempts integer NOT NULL DEFAULT 0,
            ADD COLUMN last_delivery_error text,
            ADD COLUMN last_sent_at timestamptz
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX tenant_invitations_one_pending ON tenant_invitations (tenant_id, email) "
        "WHERE status = 'PENDING'"
    )
    op.execute(
        """
        CREATE FUNCTION invitation_tenant_by_token_hash(p_token_hash text)
        RETURNS uuid
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
            SELECT tenant_id FROM public.tenant_invitations WHERE token_hash = p_token_hash
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION invitation_tenant_by_token_hash(text) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION invitation_tenant_by_token_hash(text) TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP FUNCTION invitation_tenant_by_token_hash(text)")
    op.execute("DROP INDEX tenant_invitations_one_pending")
    op.execute(
        """
        ALTER TABLE tenant_invitations
            DROP COLUMN delivery_status,
            DROP COLUMN delivery_attempts,
            DROP COLUMN last_delivery_error,
            DROP COLUMN last_sent_at
        """
    )
