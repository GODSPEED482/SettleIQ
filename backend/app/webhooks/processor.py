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

from app.audit import write_audit
from app.authz.access import EffectiveAccess
from app.authz.catalogue import get_catalogue
from app.authz.guard import TenantContext
from app.authz.workers import worker_principal
from app.db import global_tx, scoped_tx, tenant_tx
from app.domain import runs
from app.domain_packs.manifest import load_domain_packs
from app.errors import ApiError, conflict, not_found
from app.semantics.store import active_namespaces
from app.sources import conventions, evidence, records
from app.sources.envelope import SourceRecordEnvelope

logger = logging.getLogger("settleiq.webhooks.processor")

RETRY_BASE_SECONDS = 30


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

            # 4. Build the canonical record. The whole event is the attributes, so mappings
            # read `$.resource.*` and the event's own fields alike (finops mapping_hints).
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
                attributes=payload,
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
        run_ids = []
        try:
            with scoped_tx(tenant_id) as conn:
                worker_p = worker_principal(conn, tenant_id)
                namespaces = sorted(set(active_namespaces(conn)) & set(load_domain_packs()))
            ctx = TenantContext(
                tenant_id=tenant_id,
                principal=worker_p,
                access=EffectiveAccess([], get_catalogue()),
            )
            for namespace in namespaces:
                try:
                    run = runs.start_run(ctx, runs.RunBody(namespace=namespace))
                    run_ids.append(run["id"])
                except ApiError as refused:  # e.g. nothing published yet for this pack
                    logger.info("No %s run after webhook %s: %s", namespace, claimed.id,
                                refused.message)
        except Exception:
            # The event is durably processed either way; the next run picks its record up.
            logger.exception("Domain run trigger following webhook %s failed", claimed.id)

        return {"status": "PROCESSED", "inbox_id": str(claimed.id), "run_ids": run_ids}

    except Exception as exc:
        logger.exception("Failed to process webhook inbox row %s: %s", claimed.id, exc)
        with scoped_tx(tenant_id) as conn:
            new_status = "DEAD_LETTER" if claimed.attempts >= claimed.max_attempts else "FAILED"
            conn.execute(
                text(
                    """
                    UPDATE webhook_inbox
                    SET status = :status, last_error = :err,
                        next_attempt_at = now() + make_interval(secs => :wait)
                    WHERE id = :id AND tenant_id = :tenant
                    """
                ),
                {
                    "status": new_status,
                    # Exponential backoff before the next claim (migration 0014).
                    "wait": RETRY_BASE_SECONDS * 2 ** (claimed.attempts - 1),
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


REPLAYABLE = ("FAILED", "DEAD_LETTER")

_INBOX_COLUMNS = """
    id, provider_event_id, event_type, status, attempts, max_attempts, last_error,
    provider_timestamp, received_at, processed_at, next_attempt_at
"""


def _uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError:
        raise not_found() from None


def _inbox_view(r: Any) -> dict[str, Any]:
    out = dict(r._mapping)
    out["id"] = str(out["id"])
    for key in ("provider_timestamp", "received_at", "processed_at", "next_attempt_at"):
        out[key] = out[key].isoformat() if out[key] else None
    return out


def list_inbox_rows(ctx: TenantContext, source_id: str) -> list[dict[str, Any]]:
    """One source's delivery history for the Sources screen (task 4.7, spec 10.4).

    Status, attempts and errors only: payloads stay behind evidence.raw.read."""
    source_uuid = _uuid(source_id)
    with tenant_tx(ctx) as conn:
        if conn.execute(
            text("SELECT 1 FROM data_sources WHERE id = :id"), {"id": source_uuid}
        ).first() is None:
            raise not_found()
        rows = conn.execute(
            text(
                f"""
                SELECT {_INBOX_COLUMNS} FROM webhook_inbox
                WHERE source_id = :source
                ORDER BY received_at DESC, id
                LIMIT 100
                """
            ),
            {"source": source_uuid},
        ).all()
    return [_inbox_view(r) for r in rows]


def replay_webhook(ctx: TenantContext, source_id: str, inbox_id: str) -> dict[str, Any]:
    """Reprocess one failed or dead-lettered event of this source, now.

    The row keeps its provider event ID, so a replay collapses onto any record the event
    already produced (spec 10.4). The replay claims the row itself, as the processor would,
    so the background processor cannot take it at the same time."""
    source_uuid, inbox_uuid = _uuid(source_id), _uuid(inbox_id)
    with tenant_tx(ctx) as conn:
        row = conn.execute(
            text(
                """
                SELECT id, tenant_id, source_id, provider_event_id, event_type,
                    raw_evidence_id, status, max_attempts
                FROM webhook_inbox
                WHERE id = :id AND source_id = :source
                FOR UPDATE
                """
            ),
            {"id": inbox_uuid, "source": source_uuid},
        ).first()
        if row is None:
            raise not_found()
        if row.status not in REPLAYABLE:
            raise conflict("WEBHOOK_NOT_REPLAYABLE", "Only failed or dead-lettered events replay.")
        conn.execute(
            text(
                """
                UPDATE webhook_inbox
                SET status = 'PROCESSING', attempts = 1, last_error = NULL, next_attempt_at = NULL
                WHERE id = :id
                """
            ),
            {"id": row.id},
        )
        write_audit(
            conn, ctx.tenant_id, ctx.principal, "source.webhook.replayed", "source",
            {"source_id": str(source_uuid), "inbox_id": str(row.id),
             "provider_event_id": row.provider_event_id, "previous_status": row.status},
        )

    process_claimed_row(
        ClaimedInboxRow(
            id=row.id,
            tenant_id=row.tenant_id,
            source_id=row.source_id,
            provider_event_id=row.provider_event_id,
            event_type=row.event_type,
            raw_evidence_id=row.raw_evidence_id,
            attempts=1,
            max_attempts=row.max_attempts,
        )
    )
    with tenant_tx(ctx) as conn:
        after = conn.execute(
            text(f"SELECT {_INBOX_COLUMNS} FROM webhook_inbox WHERE id = :id"), {"id": row.id}
        ).one()
    return _inbox_view(after)
