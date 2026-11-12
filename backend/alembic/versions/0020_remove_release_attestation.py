"""Remove POL-14 release attestation (spec v1.8, CHG-1.8-01).

A scope's mode is now the tenant's decision alone, so the release tables, the attestation IDs
on execution-mode rows, decisions and grants, and the check that a LIVE row carries one all go.
Dropping `execution_mode_settings.attestation_id` drops its LIVE check with it. The grant guard
is replaced so that it no longer compares the dropped column.

Revision ID: 0020
Revises: 0019
"""

import importlib.util
from pathlib import Path

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def _grant_guard(attestation: bool) -> str:
    column = "NEW.attestation_id, " if attestation else ""
    old_column = "OLD.attestation_id, " if attestation else ""
    return f"""
        CREATE OR REPLACE FUNCTION guard_execution_grant() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            ex executions%ROWTYPE;
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.consumed_at IS NOT NULL THEN
                    RAISE EXCEPTION 'a grant is issued unconsumed';
                END IF;
                SELECT * INTO ex FROM executions WHERE id = NEW.execution_id;
                IF (ex.request_id, ex.request_policy_decision_id, ex.execution_policy_decision_id,
                    ex.approval_id, ex.playbook_version, ex.connector_id, ex.action_capability,
                    ex.target, ex.idempotency_key)
                   IS DISTINCT FROM
                   (NEW.request_id, NEW.request_policy_decision_id,
                    NEW.execution_policy_decision_id, NEW.approval_id, NEW.playbook_version,
                    NEW.connector_id, NEW.action_capability, NEW.target, NEW.idempotency_key) THEN
                    RAISE EXCEPTION 'a grant is bound to exactly its execution';
                END IF;
                PERFORM check_execution_authority(NEW.request_id, NEW.request_policy_decision_id,
                    NEW.execution_policy_decision_id, NEW.approval_id);
                RETURN NEW;
            END IF;
            IF OLD.consumed_at IS NOT NULL THEN
                RAISE EXCEPTION 'a grant is consumed once';
            END IF;
            IF NEW.consumed_at IS NULL OR now() >= OLD.expires_at THEN
                RAISE EXCEPTION 'only an unexpired grant can be consumed';
            END IF;
            IF (NEW.tenant_id, NEW.execution_id, NEW.request_id, NEW.request_policy_decision_id,
                NEW.execution_policy_decision_id, NEW.approval_id, NEW.playbook_version,
                NEW.connector_id, NEW.action_capability, NEW.target, NEW.idempotency_key,
                NEW.permission_keys, NEW.token_hash, {column}NEW.issued_at, NEW.expires_at)
               IS DISTINCT FROM
               (OLD.tenant_id, OLD.execution_id, OLD.request_id, OLD.request_policy_decision_id,
                OLD.execution_policy_decision_id, OLD.approval_id, OLD.playbook_version,
                OLD.connector_id, OLD.action_capability, OLD.target, OLD.idempotency_key,
                OLD.permission_keys, OLD.token_hash, {old_column}OLD.issued_at, OLD.expires_at) THEN
                RAISE EXCEPTION 'consuming a grant changes nothing else';
            END IF;
            RETURN NEW;
        END
        $$
        """


def _revision_0019():
    path = Path(__file__).with_name("0019_release_attestation.py")
    spec = importlib.util.spec_from_file_location("revision_0019", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def upgrade() -> None:
    _revision_0019().downgrade()
    op.execute("ALTER TABLE execution_mode_settings DROP COLUMN attestation_id")
    op.execute("ALTER TABLE policy_decisions DROP COLUMN attestation_id")
    op.execute(_grant_guard(attestation=False))
    op.execute("ALTER TABLE execution_grants DROP COLUMN attestation_id")
    # The seed only adds and updates catalogue keys, so the retired key is removed here.
    op.execute(
        "DELETE FROM permission_set_items WHERE permission_key = 'platform.release.attest'"
    )
    op.execute("DELETE FROM permissions WHERE key = 'platform.release.attest'")


def downgrade() -> None:
    # Rows written without an attestation cannot be given one, so the restored columns are
    # nullable and the LIVE check is not validated against existing rows. The retired
    # permission key comes back when the seed of the earlier catalogue runs.
    op.execute("ALTER TABLE execution_grants ADD COLUMN attestation_id text")
    op.execute(_grant_guard(attestation=True))
    op.execute("ALTER TABLE policy_decisions ADD COLUMN attestation_id text")
    op.execute("ALTER TABLE execution_mode_settings ADD COLUMN attestation_id uuid")
    op.execute(
        "ALTER TABLE execution_mode_settings ADD CONSTRAINT execution_mode_settings_check "
        "CHECK ((mode = 'LIVE') = (attestation_id IS NOT NULL)) NOT VALID"
    )
    _revision_0019().upgrade()
