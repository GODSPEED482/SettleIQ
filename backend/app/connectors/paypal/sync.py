"""PayPal Transaction Search backfill and capture sync (task 4.2, spec 10.3).

Sync authenticates with OAuth client credentials (AC-PP-01), then backfills through
Transaction Search (transactions.read) in windows of at most 31 days, paging until done,
and fetches details (captures.read) for each capture found. Stores each response as RECORD
evidence with provenance `provider_api` and persists records as conventions.py describes
for API reads.
"""

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from app.audit import write_audit
from app.authz.guard import TenantContext
from app.connectors.paypal.client import PayPalClient
from app.db import tenant_tx
from app.errors import validation_failed
from app.sources import conventions, evidence, records
from app.sources.envelope import SourceRecordEnvelope
from app.sources.evidence import RetrievalContext
from app.sources.secrets import resolve_secret

logger = logging.getLogger("settleiq.connectors.paypal.sync")


def _parse_credentials(secret_value: str) -> tuple[str, str]:
    secret_value = secret_value.strip()
    if secret_value.startswith("{"):
        try:
            data = json.loads(secret_value)
            return data["client_id"], data["client_secret"]
        except Exception as exc:
            raise validation_failed(f"Invalid JSON secret credentials: {exc}") from exc
    if ":" in secret_value:
        client_id, client_secret = secret_value.split(":", 1)
        return client_id.strip(), client_secret.strip()
    raise validation_failed("PayPal secret must be JSON or 'client_id:client_secret'.")


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except Exception:
        return None


def sync_paypal(
    ctx: TenantContext,
    source: Any,
    client: PayPalClient | None = None,
) -> dict[str, Any]:
    secret_val = resolve_secret(source.secret_reference)
    if client is None:
        client_id, client_secret = _parse_credentials(secret_val)
        client = PayPalClient(client_id, client_secret)

    # 30-day default window
    end_date = datetime.now(UTC)
    start_date = end_date - timedelta(days=30)

    # Search transactions
    page = 1
    captures_found: list[str] = []
    total_records = 0

    with tenant_tx(ctx) as conn:
        conventions.ensure_object(
            conn,
            tenant_id=ctx.tenant_id,
            source_id=source.id,
            object_name="transactions.read",
            object_type="API",
        )
        capture_obj_id = conventions.ensure_object(
            conn,
            tenant_id=ctx.tenant_id,
            source_id=source.id,
            object_name="captures.read",
            object_type="API",
        )

        while True:
            txn_data, raw_bytes = client.transactions_search(
                start_date=start_date,
                end_date=end_date,
                page=page,
                page_size=100,
            )

            # Store Transaction Search evidence
            evidence.store_bytes(
                conn,
                tenant_id=ctx.tenant_id,
                source_id=source.id,
                data=raw_bytes,
                evidence_type="RECORD",
                content_type="application/json",
                retrieval_context=RetrievalContext(
                    provenance="provider_api",
                    request={
                        "method": "GET",
                        "path": "/v1/reporting/transactions",
                        "page": page,
                    },
                ),
            )

            txn_details = txn_data.get("transaction_details", [])
            for item in txn_details:
                txn_info = item.get("transaction_info", {})
                txn_id = txn_info.get("transaction_id")
                # Identify capture IDs or transaction IDs
                if txn_id:
                    captures_found.append(txn_id)

            total_pages = int(txn_data.get("total_pages", 1))
            if page >= total_pages or not txn_details:
                break
            page += 1

        # Fetch detail for each capture found
        for cap_id in set(captures_found):
            try:
                cap_data, cap_bytes = client.captures_read(cap_id)
            except Exception as exc:
                logger.warning("Could not read capture %s: %s", cap_id, exc)
                continue

            raw_ev = evidence.store_bytes(
                conn,
                tenant_id=ctx.tenant_id,
                source_id=source.id,
                data=cap_bytes,
                evidence_type="RECORD",
                content_type="application/json",
                retrieval_context=RetrievalContext(
                    provenance="provider_api",
                    request={
                        "method": "GET",
                        "path": f"/v2/payments/captures/{cap_id}",
                    },
                ),
            )

            occurred = _parse_time(cap_data.get("create_time"))
            envelope = SourceRecordEnvelope(
                id=uuid4(),
                tenant_id=ctx.tenant_id,
                source_id=source.id,
                source_object_id=capture_obj_id,
                external_id=cap_id,
                record_type="paypal:record/captures.read",
                attributes=cap_data,
                raw_evidence_id=raw_ev.id,
                occurred_at=occurred,
                observed_at=datetime.now(UTC),
                payload_hash=raw_ev.content_hash,
                connector_version=source.connector_version,
                mapping_version=None,
            )
            records.persist_record(conn, envelope)
            total_records += 1

        objects = [
            {"object_name": "transactions.read", "row_count": len(captures_found)},
            {"object_name": "captures.read", "row_count": total_records},
        ]
        write_audit(
            conn,
            ctx.tenant_id,
            ctx.principal,
            "source.synced",
            "source",
            {"source_id": str(source.id), "objects": objects, "record_count": total_records},
        )

    return {"source_id": str(source.id), "objects": objects, "record_count": total_records}
