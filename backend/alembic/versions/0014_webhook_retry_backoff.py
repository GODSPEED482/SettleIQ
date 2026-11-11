"""Back off between webhook processing attempts (task 4.4, spec 10.4, NFR-011).

A failed inbox row was claimable again at once, so one pass of the processor spent every
attempt back to back and dead-lettered an event that a moment's wait might have saved. A failed
row now waits until `next_attempt_at`; the processor sets it, and a replay clears it.

Revision ID: 0014
Revises: 0013
"""

from alembic import op

from app.migration_support import APP_ROLE

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

_CLAIM = """
    CREATE OR REPLACE FUNCTION claim_webhook_inbox()
    RETURNS TABLE (
        id uuid, tenant_id uuid, source_id uuid, provider_event_id text, event_type text,
        raw_evidence_id uuid, attempts integer, max_attempts integer
    )
    LANGUAGE sql VOLATILE SECURITY DEFINER
    SET search_path = pg_catalog, public
    AS $$
        UPDATE public.webhook_inbox w
        SET status = 'PROCESSING', attempts = w.attempts + 1
        WHERE w.id = (
            SELECT q.id FROM public.webhook_inbox q
            WHERE q.status IN ('RECEIVED', 'FAILED') AND q.attempts < q.max_attempts
              {due}
            ORDER BY q.received_at
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        )
        RETURNING w.id, w.tenant_id, w.source_id, w.provider_event_id, w.event_type,
                  w.raw_evidence_id, w.attempts, w.max_attempts
    $$
"""


def _claim(due: str) -> None:
    op.execute(_CLAIM.format(due=due))
    op.execute("REVOKE ALL ON FUNCTION claim_webhook_inbox() FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION claim_webhook_inbox() TO {APP_ROLE}")


def upgrade() -> None:
    op.execute("ALTER TABLE webhook_inbox ADD COLUMN next_attempt_at timestamptz")
    _claim("AND (q.next_attempt_at IS NULL OR q.next_attempt_at <= now())")


def downgrade() -> None:
    _claim("")
    op.execute("ALTER TABLE webhook_inbox DROP COLUMN next_attempt_at")
