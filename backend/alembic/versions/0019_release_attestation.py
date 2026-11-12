"""Release attestations, signed revocation states and the release_attestor (spec 9.7).

Stored envelopes are copies of what the attestation workflow wrote to write-once storage. They
are untrusted: the verifier re-checks the signature over the exact stored bytes on every use
and reads scope only from them, so the lookup columns here merely find candidates. Only the
owner role imports them (`python -m app.release.store`); the runtime role can read them.

Revocation states are append-only, and a monotonic high-water mark records the highest
sequence ever imported, so deleting the newest state cannot bring back an older one that does
not list a revoked attestation (AC-REL-08).

Revision ID: 0019
Revises: 0018
"""

from alembic import op

from app.migration_support import APP_ROLE, enable_tenant_rls

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE platform_service_principals (
            id uuid PRIMARY KEY,
            kind text NOT NULL UNIQUE CHECK (kind IN ('release_attestor')),
            permissions text[] NOT NULL CHECK (permissions = ARRAY['platform.release.attest']),
            -- The signing key it is bound to; set when Rishav provisions the key.
            key_id text,
            status text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'DISABLED')),
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        INSERT INTO platform_service_principals (id, kind, permissions)
        VALUES (gen_random_uuid(), 'release_attestor', ARRAY['platform.release.attest'])
        """
    )
    op.execute(
        """
        CREATE TABLE release_gate_attestations (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants (id),
            connector_id text NOT NULL,
            action_capability text NOT NULL,
            envelope bytea NOT NULL,
            envelope_sha256 text NOT NULL UNIQUE CHECK (envelope_sha256 ~ '^[0-9a-f]{64}$'),
            imported_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX release_gate_attestations_scope_idx "
        "ON release_gate_attestations (tenant_id, connector_id, action_capability, imported_at)"
    )
    op.execute(
        """
        CREATE TABLE release_revocation_states (
            sequence bigint PRIMARY KEY CHECK (sequence > 0),
            envelope bytea NOT NULL,
            envelope_sha256 text NOT NULL UNIQUE CHECK (envelope_sha256 ~ '^[0-9a-f]{64}$'),
            imported_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE release_revocation_high_water (
            singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
            sequence bigint NOT NULL CHECK (sequence >= 0)
        )
        """
    )
    op.execute("INSERT INTO release_revocation_high_water (sequence) VALUES (0)")
    op.execute(
        """
        CREATE FUNCTION refuse_release_change() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
        END
        $$
        """
    )
    for table in ("release_gate_attestations", "release_revocation_states"):
        op.execute(
            f"""
            CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION refuse_release_change()
            """
        )
    op.execute(
        """
        CREATE FUNCTION guard_revocation_high_water() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'the revocation high-water mark cannot be removed';
            END IF;
            IF NEW.sequence < OLD.sequence THEN
                RAISE EXCEPTION 'the revocation high-water mark only rises';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER release_revocation_high_water_guard
        BEFORE UPDATE OR DELETE ON release_revocation_high_water
        FOR EACH ROW EXECUTE FUNCTION guard_revocation_high_water()
        """
    )
    enable_tenant_rls("release_gate_attestations")
    for table in ("release_gate_attestations", "release_revocation_states",
                  "release_revocation_high_water"):
        op.execute(f"GRANT SELECT ON {table} TO {APP_ROLE}")


def downgrade() -> None:
    for table in ("release_revocation_high_water", "release_revocation_states",
                  "release_gate_attestations", "platform_service_principals"):
        op.execute(f"DROP TABLE {table}")
    op.execute("DROP FUNCTION guard_revocation_high_water()")
    op.execute("DROP FUNCTION refuse_release_change()")
