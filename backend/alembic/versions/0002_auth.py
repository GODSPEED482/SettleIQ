"""Credentials, verification tokens, sessions and the list-my-tenants lookup (spec 3.1, 11.2).

These tables hold global identity and have no tenant_id, like `users`. Tokens are stored only
as hashes. `tenants_for_user` is the one path to memberships before a tenant is selected: RLS
hides them until then, so it runs as the migration owner and returns only that user's tenants.

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

APP_ROLE = "settleiq_app"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE user_credentials (
            user_id uuid PRIMARY KEY REFERENCES users (id),
            password_hash text NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE email_verification_tokens (
            id uuid PRIMARY KEY,
            user_id uuid NOT NULL REFERENCES users (id),
            token_hash text NOT NULL UNIQUE,
            expires_at timestamptz NOT NULL,
            consumed_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX email_verification_user_idx ON email_verification_tokens (user_id)")
    op.execute(
        """
        CREATE TABLE auth_sessions (
            id uuid PRIMARY KEY,
            user_id uuid NOT NULL REFERENCES users (id),
            token_hash text NOT NULL UNIQUE,
            created_at timestamptz NOT NULL DEFAULT now(),
            expires_at timestamptz NOT NULL,
            revoked_at timestamptz
        )
        """
    )
    op.execute(
        """
        CREATE TABLE auth_rate_limits (
            bucket text NOT NULL,
            window_start timestamptz NOT NULL,
            hits integer NOT NULL,
            PRIMARY KEY (bucket, window_start)
        )
        """
    )
    for table in (
        "user_credentials",
        "email_verification_tokens",
        "auth_sessions",
        "auth_rate_limits",
    ):
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON {table} TO {APP_ROLE}")

    op.execute(
        """
        CREATE FUNCTION tenants_for_user(p_user_id uuid)
        RETURNS TABLE (tenant_id uuid, name text)
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
            SELECT t.id, t.name
            FROM public.tenant_members m
            JOIN public.tenants t ON t.id = m.tenant_id
            WHERE m.user_id = p_user_id AND m.status = 'ACTIVE'
            ORDER BY t.created_at, t.id
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION tenants_for_user(uuid) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION tenants_for_user(uuid) TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP FUNCTION tenants_for_user(uuid)")
    for table in (
        "auth_rate_limits",
        "auth_sessions",
        "email_verification_tokens",
        "user_credentials",
    ):
        op.execute(f"DROP TABLE {table}")
