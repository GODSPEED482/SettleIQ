"""A source record is one observation, so a changed payload is a new record (OPEN-04).

Before this, a re-read of the same provider resource whose state had changed returned the
first record and dropped the new observation, which would defeat readback (spec 9.4).
Identical replays still collapse onto one record.

Downgrade fails if any external ID already has more than one observation; that is deliberate,
because it would otherwise have to delete evidence-backed records.

Revision ID: 0009
Revises: 0008
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

OLD = "source_records_tenant_id_source_id_source_object_id_externa_key"
NEW = "source_records_observation_key"


def upgrade() -> None:
    op.execute(f"ALTER TABLE source_records DROP CONSTRAINT {OLD}")
    op.execute(
        f"ALTER TABLE source_records ADD CONSTRAINT {NEW} "
        "UNIQUE (tenant_id, source_id, source_object_id, external_id, payload_hash)"
    )


def downgrade() -> None:
    op.execute(f"ALTER TABLE source_records DROP CONSTRAINT {NEW}")
    op.execute(
        f"ALTER TABLE source_records ADD CONSTRAINT {OLD} "
        "UNIQUE (tenant_id, source_id, source_object_id, external_id)"
    )
