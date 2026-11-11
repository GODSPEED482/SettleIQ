"""Asynchronous webhook inbox processing (task 4.4, spec 6.3, 10.4, AC-PP-05).

Processes webhook inbox rows asynchronously apart from receipt.
- Claims rows with `FOR UPDATE SKIP LOCKED`.
- Persists canonical source records according to conventions.py.
- Triggers domain runs for active domain packs so captures reach reconciliation (AC-PP-05).
- Retries failed rows up to max_attempts, then marks them DEAD_LETTER for human review.
- Replays reuse the original event identity and do not duplicate downstream work.
- Exposes failed, replayed and dead-lettered rows to the Sources screen (task 4.7).
"""

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, text

from app.authz.access import EffectiveAccess, Principal, PrincipalKind
from app.authz.catalogue import get_catalogue
from app.authz.guard import TenantContext
from app.db import global_tx, scoped_tx
from app.domain import runs
from app.errors import not_found
from app.sources import conventions, evidence, records
from app.sources.envelope import SourceRecordEnvelope

logger = logging.getLogger("settleiq.webhooks.processor")


@dataclass(frozen=True)
class ClaimedInboxRow:
    id: UUID
    tenant_id: UUID
    source_id: UUID
    provider_event_id: str
    event_type: str
    raw_evidence_id: UUID
    attempts: int
    max_attempts: int


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _get_worker_principal(conn: Connection, tenant_id: UUID) -> Principal:
    row = conn.execute(
        text(
            """
            SELECT id FROM service_principals
            WHERE tenant_id = :tenant AND kind = 'worker' AND status = 'ACTIVE'
            LIMIT 1
            """
        ),
        {"tenant": tenant_id},
    ).first()
    if row is not None:
        return Principal(row.id, PrincipalKind.WORKER, email_verified=True)
    # Default fallback worker identity if no service principal was explicitly created
    return Principal(uuid4(), PrincipalKind.WORKER, email_verified=True)


def claim_inbox_row(conn: Connection) -> ClaimedInboxRow | None:
    """Claim one pending or retryable webhook inbox row across all tenants.

    RLS hides inbox rows without tenant context, so the claim goes through the
    `claim_webhook_inbox()` SECURITY DEFINER function (migration 0012)."""
    row = conn.execute(text("SELECT * FROM claim_webhook_inbox()")).first()
    if row is None:
        return None
    return ClaimedInboxRow(
        id=row.id,
        tenant_id=row.tenant_id,
        source_id=row.source_id,
        provider_event_id=row.provider_event_id,
        event_type=row.event_type,
        raw_evidence_id=row.raw_evidence_id,
        attempts=row.attempts,
        max_attempts=row.max_attempts,
    )


def process_claimed_row(claimed: ClaimedInboxRow) -> dict[str, Any]:
    """Process a claimed inbox row in a tenant-scoped transaction."""
    tenant_id = claimed.tenant_id
    try:
        with scoped_tx(tenant_id) as conn:
            # 1. Read and verify raw evidence bytes
            raw_bytes = evidence.read_verified(conn, claimed.raw_evidence_id)
            payload = json.loads(raw_bytes.decode("utf-8"))

            # 2. Get connector details
            source_row = conn.execute(
                text("SELECT connector_id, connector_version FROM data_sources WHERE id = :id"),
                {"id": claimed.source_id},
            ).one()

            # 3. Ensure 'events' source object exists
            event_obj_id = conventions.ensure_object(
                conn,
                tenant_id=tenant_id,
                source_id=claimed.source_id,
                object_name=conventions.EVENT_OBJECT,
                object_type="EVENT",
            )

            # 4. Extract resource and build canonical record
            resource = payload.get("resource", {})
            event_time = _parse_time(payload.get("create_time")) or datetime.now(UTC)
            raw_digest = conn.execute(
                text("SELECT content_hash FROM raw_evidence WHERE id = :id"),
                {"id": claimed.raw_evidence_id},
            ).scalar_one()

            rec_type = conventions.event_record_type(source_row.connector_id, claimed.event_type)
            envelope = SourceRecordEnvelope(
                id=uuid4(),
                tenant_id=tenant_id,
                source_id=claimed.source_id,
                source_object_id=event_obj_id,
                external_id=claimed.provider_event_id,
                record_type=rec_type,
                attributes=resource,
                raw_evidence_id=claimed.raw_evidence_id,
                occurred_at=event_time,
                observed_at=datetime.now(UTC),
                payload_hash=raw_digest,
                connector_version=source_row.connector_version,
                mapping_version=None,
            )

            # Replays reuse existing external_id without error (conventions.py / spec 10.4)
            existing_record = conn.execute(
                text(
                    """
                    SELECT id FROM source_records
                    WHERE tenant_id = :tenant AND source_id = :source
                      AND source_object_id = :obj AND external_id = :ext
                    """
                ),
                {
                    "tenant": tenant_id,
                    "source": claimed.source_id,
                    "obj": event_obj_id,
                    "ext": claimed.provider_event_id,
                },
            ).first()

            if existing_record is None:
                records.persist_record(conn, envelope)

            # 5. Mark webhook inbox row as PROCESSED
            conn.execute(
                text(
                    """
                    UPDATE webhook_inbox
                    SET status = 'PROCESSED', processed_at = now(), last_error = NULL
                    WHERE id = :id AND tenant_id = :tenant
                    """
                ),
                {"id": claimed.id, "tenant": tenant_id},
            )

        # 6. Trigger domain run so capture reaches reconciliation (AC-PP-05)
        # Runs outside the inbox transaction so run failures don't roll back the inbox row
        try:
            with scoped_tx(tenant_id) as conn:
                worker_p = _get_worker_principal(conn, tenant_id)
                ctx = TenantContext(
                    tenant_id=tenant_id,
                    principal=worker_p,
                    access=EffectiveAccess([], get_catalogue()),
                )
                runs.start_run(ctx, runs.RunBody(namespace="finops"))
        except Exception as run_err:
            logger.warning(
                "Domain run trigger following webhook %s had notice: %s", claimed.id, run_err
            )

        return {"status": "PROCESSED", "inbox_id": str(claimed.id)}

    except Exception as exc:
        logger.exception("Failed to process webhook inbox row %s: %s", claimed.id, exc)
        with scoped_tx(tenant_id) as conn:
            new_status = "DEAD_LETTER" if claimed.attempts >= claimed.max_attempts else "FAILED"
            conn.execute(
                text(
                    """
                    UPDATE webhook_inbox
                    SET status = :status, last_error = :err
                    WHERE id = :id AND tenant_id = :tenant
                    """
                ),
                {
                    "status": new_status,
                    "err": str(exc)[:500],
                    "id": claimed.id,
                    "tenant": tenant_id,
                },
            )
        return {"status": new_status, "inbox_id": str(claimed.id), "error": str(exc)}


def claim_and_process_pending_webhooks(limit: int = 50) -> list[dict[str, Any]]:
    """Process pending webhook rows up to the given limit."""
    results = []
    for _ in range(limit):
        with global_tx() as conn:
            claimed = claim_inbox_row(conn)
        if claimed is None:
            break
        res = process_claimed_row(claimed)
        results.append(res)
    return results


def replay_webhook(tenant_id: UUID, inbox_id: UUID) -> dict[str, Any]:
    """Reset a failed or dead-lettered webhook inbox row and reprocess it."""
    with scoped_tx(tenant_id) as conn:
        row = conn.execute(
            text(
                """
                UPDATE webhook_inbox
                SET status = 'RECEIVED', attempts = 0, last_error = NULL
                WHERE id = :id AND tenant_id = :tenant
                RETURNING id, tenant_id, source_id, provider_event_id, event_type,
                          raw_evidence_id, attempts, max_attempts
                """
            ),
            {"id": inbox_id, "tenant": tenant_id},
        ).first()
        if row is None:
            raise not_found()

    claimed = ClaimedInboxRow(
        id=row.id,
        tenant_id=row.tenant_id,
        source_id=row.source_id,
        provider_event_id=row.provider_event_id,
        event_type=row.event_type,
        raw_evidence_id=row.raw_evidence_id,
        attempts=0,
        max_attempts=row.max_attempts,
    )
    return process_claimed_row(claimed)


def list_inbox_rows(conn: Connection, tenant_id: UUID, source_id: UUID) -> list[dict[str, Any]]:
    """Query inbox rows for the Sources screen (task 4.7)."""
    rows = conn.execute(
        text(
            """
            SELECT id, provider_event_id, event_type, status, attempts, max_attempts,
                   last_error, provider_timestamp, received_at, processed_at
            FROM webhook_inbox
            WHERE tenant_id = :tenant AND source_id = :source
            ORDER BY received_at DESC
            LIMIT 100
            """
        ),
        {"tenant": tenant_id, "source": source_id},
    ).all()
    return [
        {
            "id": str(r.id),
            "provider_event_id": r.provider_event_id,
            "event_type": r.event_type,
            "status": r.status,
            "attempts": r.attempts,
            "max_attempts": r.max_attempts,
            "last_error": r.last_error,
            "provider_timestamp": (
                r.provider_timestamp.isoformat() if r.provider_timestamp else None
            ),
            "received_at": r.received_at.isoformat() if r.received_at else None,
            "processed_at": r.processed_at.isoformat() if r.processed_at else None,
        }
        for r in rows
    ]
