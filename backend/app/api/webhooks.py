"""Provider webhook ingress (spec 10.4, POL-15, AC-WEBHOOK-01, AC-PP-03, AC-PP-04).

Order is binding:
1. Verify the signature against the registered webhook ID and provider transmission metadata.
2. If unknown source or bad signature, write nothing tenant-owned; write forensics only to
   platform_security_ingress_logs (AC-PP-03).
3. Only after successful signature verification, derive tenant from the trusted source
   registration (AC-WEBHOOK-01).
4. Store original bytes in raw_evidence with provenance `provider_event` and insert a
   `webhook_inbox` row. Deduplicate repeated delivery by (tenant_id, source_id, provider_event_id)
   safely (AC-PP-04).
"""

import hashlib
import json
import logging
from datetime import datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import text

from app.authz.guard import public
from app.connectors.paypal.webhooks import verify_signature
from app.db import global_tx, scoped_tx
from app.sources import evidence, registry
from app.sources.evidence import RetrievalContext

logger = logging.getLogger("settleiq.webhooks.api")

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _log_forensics(source_id: str, reason: str, request: Request, raw_body: bytes) -> None:
    """Record forensic details in platform_security_ingress_logs with bounded metadata."""
    try:
        body_digest = hashlib.sha256(raw_body).hexdigest() if raw_body else None
        # Bounded headers only; strip sensitive values
        safe_headers = {
            k: v for k, v in request.headers.items()
            if k.lower() in ("paypal-auth-algo", "paypal-transmission-id", "paypal-cert-url",
                            "paypal-transmission-time", "content-type", "user-agent")
        }
        client_ip = request.client.host if request.client else None
        with global_tx() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO platform_security_ingress_logs
                        (id, source_id, reason, remote_ip, headers, body_hash)
                    VALUES (:id, :source, :reason, :ip, CAST(:headers AS jsonb), :hash)
                    """
                ),
                {
                    "id": uuid4(),
                    "source": str(source_id)[:100],
                    "reason": reason[:200],
                    "ip": client_ip,
                    "headers": json.dumps(safe_headers),
                    "hash": body_digest,
                },
            )
    except Exception as exc:
        logger.error("Failed to write platform security ingress log: %s", exc)


@router.post("/paypal/{source_id}")
async def paypal_webhook(
    source_id: str,
    request: Request,
    _: None = public(),
) -> None:
    raw_body = await request.body()
    try:
        source_uuid = UUID(source_id)
    except ValueError:
        _log_forensics(source_id, "invalid_uuid_source_id", request, raw_body)
        raise HTTPException(  # noqa: B904
            status_code=400, detail="Invalid webhook source or signature."
        ) from None

    try:
        payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except Exception:
        _log_forensics(source_id, "invalid_json_body", request, raw_body)
        raise HTTPException(  # noqa: B904
            status_code=400, detail="Invalid webhook source or signature."
        ) from None

    # 1. Narrowly privileged source registration lookup (returns trusted tenant, connector, config)
    source_row = registry.webhook_source(source_uuid)
    if source_row is None or source_row["connector_id"] != "paypal":
        _log_forensics(source_id, "unknown_or_inactive_source", request, raw_body)
        raise HTTPException(status_code=400, detail="Invalid webhook source or signature.")

    # Only what PUT /api/sources/{id}/webhooks registered (source.webhook.manage, AC-PP-10).
    webhook_id = ((source_row["config"] or {}).get("webhook") or {}).get("webhook_id")
    if not webhook_id:
        _log_forensics(source_id, "source_missing_registered_webhook_id", request, raw_body)
        raise HTTPException(status_code=400, detail="Invalid webhook source or signature.")

    # 2. Verify signature before trusting event content or deriving tenant context (AC-PP-03)
    is_valid, reason = verify_signature(
        dict(request.headers),
        raw_body,
        webhook_id,
        json_payload=payload,
    )
    if not is_valid:
        _log_forensics(source_id, f"signature_verification_failed: {reason}", request, raw_body)
        raise HTTPException(status_code=400, detail="Invalid webhook source or signature.")

    # 3. Only after verification, derive trusted tenant ID (never from payload: AC-WEBHOOK-01)
    trusted_tenant_id = source_row["tenant_id"]
    event_id = payload.get("id") or f"evt_{uuid4().hex}"
    event_type = payload.get("event_type") or "UNKNOWN"
    create_time = _parse_time(payload.get("create_time"))

    # 4. Open scoped_tx(trusted_tenant_id) to persist raw evidence and webhook_inbox row
    with scoped_tx(trusted_tenant_id) as conn:
        raw_ref = evidence.store_bytes(
            conn,
            tenant_id=trusted_tenant_id,
            source_id=source_uuid,
            data=raw_body,
            evidence_type="WEBHOOK",
            content_type="application/json",
            retrieval_context=RetrievalContext(
                provenance="provider_event",
                event_type=event_type,
            ),
            provider_event_id=event_id,
            source_timestamp=create_time,
        )

        # Deduplicate repeated delivery by (tenant_id, source_id, provider_event_id) (AC-PP-04)
        conn.execute(
            text(
                """
                INSERT INTO webhook_inbox (
                    id, tenant_id, source_id, provider_event_id, event_type,
                    raw_evidence_id, status, provider_timestamp
                )
                VALUES (:id, :tenant, :source, :event_id, :event_type, :raw_id, 'RECEIVED', :ts)
                ON CONFLICT (tenant_id, source_id, provider_event_id) DO NOTHING
                """
            ),
            {
                "id": uuid4(),
                "tenant": trusted_tenant_id,
                "source": source_uuid,
                "event_id": event_id,
                "event_type": event_type,
                "raw_id": raw_ref.id,
                "ts": create_time,
            },
        )

    return {"status": "received", "event_id": event_id}
