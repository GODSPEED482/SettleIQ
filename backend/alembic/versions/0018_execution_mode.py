"""Exact-scope execution mode (spec 9.5, AC-EXEC-MODE-01..04).

One row per `(tenant, domain pack, connector, action capability)`. An absent row means SHADOW,
and nothing is inherited from any broader or sibling scope. A LIVE row must carry the ID of the
POL-14 attestation the switch was verified against; returning to SHADOW clears it.

Revision ID: 0018
Revises: 0017
"""

from alembic import op

from app.migration_support import APP_ROLE, enable_tenant_rls

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        r"""
        CREATE TABLE execution_mode_settings (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants (id),
            domain_pack_id text NOT NULL CHECK (domain_pack_id ~ '^[a-z][a-z0-9_]*$'),
            domain_pack_version text NOT NULL,
            connector_id text NOT NULL CHECK (connector_id ~ '^[a-z][a-z0-9-]*$'),
            action_capability text NOT NULL CHECK (action_capability ~ '^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$'),
            mode text NOT NULL CHECK (mode IN ('SHADOW', 'LIVE')),
            attestation_id uuid,
            changed_by uuid NOT NULL REFERENCES users (id),
            changed_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, domain_pack_id, connector_id, action_capability),
            CHECK ((mode = 'LIVE') = (attestation_id IS NOT NULL))
        )
        """
    )
    op.execute(
        """
        CREATE FUNCTION guard_execution_mode_setting() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF (NEW.tenant_id, NEW.domain_pack_id, NEW.connector_id, NEW.action_capability)
               IS DISTINCT FROM
               (OLD.tenant_id, OLD.domain_pack_id, OLD.connector_id, OLD.action_capability) THEN
                RAISE EXCEPTION 'an execution-mode row keeps its exact scope';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER execution_mode_settings_guard BEFORE UPDATE ON execution_mode_settings
        FOR EACH ROW EXECUTE FUNCTION guard_execution_mode_setting()
        """
    )
    enable_tenant_rls("execution_mode_settings")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON execution_mode_settings TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP TABLE execution_mode_settings")
    op.execute("DROP FUNCTION guard_execution_mode_setting()")
