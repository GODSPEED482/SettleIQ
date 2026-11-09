"""Identity, tenancy, authorization and audit foundation (spec 3, 6.3, 11.1).

Every new tenant-owned table must call `enable_tenant_rls`. tests/test_rls.py fails
for any table with a tenant_id column that lacks enabled, forced RLS and a policy.

Revision ID: 0001
Revises:
"""

import os

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

APP_ROLE = "settleiq_app"
TENANT_PREDICATE = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def enable_tenant_rls(table: str, column: str = "tenant_id") -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} "
        f"USING ({column} = {TENANT_PREDICATE}) "
        f"WITH CHECK ({column} = {TENANT_PREDICATE})"
    )


# TODO(G1 1.2 Rishav): owner review of this migration, RLS and the role split.
# TODO(G1 1.7 Rahul): add data_sources, source_objects, raw_evidence and source_records in a new
# migration, using enable_tenant_rls and explicit GRANTs. raw_evidence gets SELECT, INSERT only.
def upgrade() -> None:
    password = os.environ["APP_DB_PASSWORD"].replace("'", "''")
    op.execute(
        f"""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                CREATE ROLE {APP_ROLE} LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD '{password}';
            END IF;
        END $$
        """
    )

    op.execute(
        """
        CREATE TABLE users (
            id uuid PRIMARY KEY,
            email text NOT NULL UNIQUE CHECK (email = lower(email)),
            email_verified_at timestamptz,
            status text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'DISABLED')),
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE tenants (
            id uuid PRIMARY KEY,
            name text NOT NULL,
            industry_labels text[] NOT NULL DEFAULT '{}',
            owner_user_id uuid NOT NULL REFERENCES users (id),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE tenant_members (
            tenant_id uuid NOT NULL REFERENCES tenants (id),
            user_id uuid NOT NULL REFERENCES users (id),
            status text NOT NULL CHECK (status IN ('ACTIVE', 'SUSPENDED', 'REMOVED')),
            joined_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, user_id)
        )
        """
    )

    # Spec 3.2: the owner pointer must reference a member of the same tenant.
    op.execute(
        """
        ALTER TABLE tenants ADD CONSTRAINT tenants_owner_is_member
            FOREIGN KEY (id, owner_user_id) REFERENCES tenant_members (tenant_id, user_id)
            DEFERRABLE INITIALLY DEFERRED
        """
    )

    # A foreign key cannot see status, so ACTIVE ownership is enforced at commit.
    op.execute(
        """
        CREATE FUNCTION enforce_owner_active_on_tenant() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM tenant_members m
                WHERE m.tenant_id = NEW.id AND m.user_id = NEW.owner_user_id
                  AND m.status = 'ACTIVE'
            ) THEN
                RAISE EXCEPTION 'tenant owner must be an ACTIVE member'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NULL;
        END $$
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER tenants_owner_active
            AFTER INSERT OR UPDATE OF owner_user_id ON tenants
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION enforce_owner_active_on_tenant()
        """
    )
    op.execute(
        """
        CREATE FUNCTION enforce_owner_active_on_member() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.status <> 'ACTIVE' AND EXISTS (
                SELECT 1 FROM tenants t
                WHERE t.id = NEW.tenant_id AND t.owner_user_id = NEW.user_id
            ) THEN
                RAISE EXCEPTION 'current owner cannot be suspended or removed before transfer'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NULL;
        END $$
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER tenant_members_owner_active
            AFTER UPDATE OF status ON tenant_members
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION enforce_owner_active_on_member()
        """
    )

    # Catalogue tables are global, system-managed seed data (app/authz/seed.py).
    op.execute(
        """
        CREATE TABLE permissions (
            key text PRIMARY KEY,
            resource text NOT NULL,
            action text NOT NULL,
            scope_type text NOT NULL CHECK (scope_type IN ('tenant', 'platform')),
            narrowable_scopes text[] NOT NULL,
            principal_types text[] NOT NULL,
            sensitivity text NOT NULL CHECK (sensitivity IN ('normal', 'sensitive', 'critical')),
            grant_requires text[] NOT NULL,
            tenant_grantable boolean NOT NULL,
            namespace text NOT NULL,
            release text NOT NULL,
            description text NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE permission_sets (
            id uuid PRIMARY KEY,
            key text NOT NULL,
            version integer NOT NULL,
            display_name text NOT NULL,
            principal_type text NOT NULL,
            status text NOT NULL CHECK (status IN ('ACTIVE', 'RETIRED')),
            derivation text CHECK (derivation IN ('OWNER_POINTER')),
            UNIQUE (key, version)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE permission_set_items (
            permission_set_id uuid NOT NULL REFERENCES permission_sets (id),
            permission_key text NOT NULL REFERENCES permissions (key),
            effect text NOT NULL CHECK (effect IN ('ALLOW', 'DENY')),
            PRIMARY KEY (permission_set_id, permission_key)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE service_principals (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants (id),
            kind text NOT NULL CHECK (kind IN ('agent', 'worker')),
            status text NOT NULL CHECK (status IN ('ACTIVE', 'DISABLED')),
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE member_grants (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants (id),
            user_id uuid,
            service_principal_id uuid,
            permission_set_id uuid NOT NULL REFERENCES permission_sets (id),
            scope_type text,
            scope_id text,
            conditions jsonb NOT NULL DEFAULT '{}',
            granted_by uuid REFERENCES users (id),
            expires_at timestamptz,
            revoked_at timestamptz,
            revoked_by uuid REFERENCES users (id),
            created_at timestamptz NOT NULL DEFAULT now(),
            FOREIGN KEY (tenant_id, user_id) REFERENCES tenant_members (tenant_id, user_id),
            FOREIGN KEY (tenant_id, service_principal_id)
                REFERENCES service_principals (tenant_id, id),
            CHECK (num_nonnulls(user_id, service_principal_id) = 1),
            CHECK ((scope_type IS NULL) = (scope_id IS NULL))
        )
        """
    )
    op.execute("CREATE INDEX member_grants_user_idx ON member_grants (tenant_id, user_id)")

    # TODO(G1 1.5 Rishav): add delivery state in a new migration; there is no column for it,
    # and AC-IAM-09 needs delivery failures visible and retryable.
    op.execute(
        """
        CREATE TABLE tenant_invitations (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants (id),
            email text NOT NULL CHECK (email = lower(email)),
            token_hash text NOT NULL UNIQUE,
            status text NOT NULL
                CHECK (status IN ('PENDING', 'ACCEPTED', 'REVOKED', 'EXPIRED', 'SUPERSEDED')),
            expires_at timestamptz NOT NULL,
            invited_by uuid NOT NULL REFERENCES users (id),
            created_at timestamptz NOT NULL DEFAULT now(),
            accepted_at timestamptz,
            revoked_at timestamptz,
            UNIQUE (tenant_id, id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE tenant_invitation_grants (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            invitation_id uuid NOT NULL,
            permission_set_id uuid NOT NULL REFERENCES permission_sets (id),
            scope_type text,
            scope_id text,
            conditions jsonb NOT NULL DEFAULT '{}',
            FOREIGN KEY (tenant_id, invitation_id) REFERENCES tenant_invitations (tenant_id, id),
            CHECK ((scope_type IS NULL) = (scope_id IS NULL))
        )
        """
    )

    # TODO(G1 1.4 Rishav): owner review; ownership transfer writes its event here (spec 3.2).
    op.execute(
        """
        CREATE TABLE audit_logs (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants (id),
            principal_kind text NOT NULL,
            principal_id uuid NOT NULL,
            invoker_user_id uuid,
            action text NOT NULL,
            permission_key text,
            resource text,
            scope jsonb,
            decision text,
            policy_rules text[] NOT NULL DEFAULT '{}',
            detail jsonb NOT NULL DEFAULT '{}',
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX audit_logs_tenant_idx ON audit_logs (tenant_id, created_at)")

    enable_tenant_rls("tenants", column="id")
    for table in (
        "tenant_members",
        "service_principals",
        "member_grants",
        "tenant_invitations",
        "tenant_invitation_grants",
        "audit_logs",
    ):
        enable_tenant_rls(table)

    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON users TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON tenants TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON tenant_members TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON service_principals TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON member_grants TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON tenant_invitations TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON tenant_invitation_grants TO {APP_ROLE}")
    # Audit is append-only for the application.
    op.execute(f"GRANT SELECT, INSERT ON audit_logs TO {APP_ROLE}")
    op.execute(f"GRANT SELECT ON permissions, permission_sets, permission_set_items TO {APP_ROLE}")


def downgrade() -> None:
    for table in (
        "audit_logs",
        "tenant_invitation_grants",
        "tenant_invitations",
        "member_grants",
        "service_principals",
        "permission_set_items",
        "permission_sets",
        "permissions",
    ):
        op.execute(f"DROP TABLE {table}")
    op.execute("ALTER TABLE tenants DROP CONSTRAINT tenants_owner_is_member")
    op.execute("DROP TABLE tenant_members")
    op.execute("DROP TABLE tenants")
    op.execute("DROP TABLE users")
    op.execute("DROP FUNCTION enforce_owner_active_on_tenant()")
    op.execute("DROP FUNCTION enforce_owner_active_on_member()")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {APP_ROLE}")
    op.execute(f"DROP ROLE {APP_ROLE}")
