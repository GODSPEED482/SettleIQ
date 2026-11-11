"""Merchant PostgreSQL record reads (spec 4.4.1, 7.1, AC-REC-01, AC-REC-02).

Discovery records what a merchant database holds; sync reads the rows of its discovered
tables through the same read-only connection and normalizes them into the canonical
source-record envelope. Values stay as the source returned them: no column is dropped, a
Decimal is serialized as a string rather than a float, and domain projection is left to the
active pack (task 3.1).
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, text
from sqlalchemy.exc import SQLAlchemyError

from app.audit import write_audit
from app.authz.guard import TenantContext
from app.connectors.manifest import load_connector_manifests
from app.db import tenant_tx
from app.errors import conflict, not_found
from app.sources import evidence, records
from app.sources.conventions import database_record_type
from app.sources.envelope import SourceRecordEnvelope
from app.sources.evidence import RetrievalContext
from app.sources.postgres import read_only_engine
from app.sources.secrets import resolve_secret


@dataclass(frozen=True)
class _Table:
    id: UUID
    name: str
    schema: str
    primary_key: tuple[str, ...]


@dataclass(frozen=True)
class _Read:
    table: _Table
    rows: list[dict[str, Any]]
    payload: bytes


def _load_source(conn: Connection, source_id: UUID) -> Any:
    row = conn.execute(
        text(
            """
            SELECT id, connector_id, connector_version, protocol, config, secret_reference,
                status
            FROM data_sources WHERE id = :id
            """
        ),
        {"id": source_id},
    ).first()
    if row is None:
        raise not_found()
    if row.connector_id not in ("merchant-postgres", "paypal"):
        raise conflict("SYNC_UNSUPPORTED", "This connector does not support record reads.")
    manifest = load_connector_manifests().get(row.connector_id)
    if manifest is None or not manifest.supports_batch_extraction:
        raise conflict("SYNC_UNSUPPORTED", "This connector does not support record reads.")
    if row.status != "ACTIVE":
        raise conflict("SOURCE_INACTIVE", "Only an active source can be synced.")
    return row


def _tables(conn: Connection, source_id: UUID, config: dict[str, Any]) -> list[_Table]:
    rows = conn.execute(
        text(
            """
            SELECT id, object_name, schema_snapshot FROM source_objects
            WHERE source_id = :source AND object_type = 'TABLE'
            ORDER BY object_name
            """
        ),
        {"source": source_id},
    ).all()
    if not rows:
        raise conflict("SOURCE_NOT_DISCOVERED", "Discover the source before syncing it.")
    default_schema = config.get("schema") or "public"
    return [
        _Table(
            row.id,
            row.object_name,
            (row.schema_snapshot or {}).get("schema") or default_schema,
            tuple((row.schema_snapshot or {}).get("primary_key") or ()),
        )
        for row in rows
    ]


def _json_value(value: Any) -> Any:
    """A database value as JSON, preserving precision and every original column."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(UTC)
        return value.isoformat()
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _canonical_row(row: dict[str, Any]) -> str:
    return json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _read(secret: str, tables: list[_Table]) -> list[_Read]:
    """Read every table in one read-only transaction, before any platform write."""
    engine = read_only_engine(secret)
    reads: list[_Read] = []
    try:
        with engine.connect() as conn:
            for table in tables:
                quote = conn.dialect.identifier_preparer.quote
                relation = f"{quote(table.schema)}.{quote(table.name)}"
                result = conn.execute(text(f"SELECT * FROM {relation}"))
                rows = [
                    {key: _json_value(value) for key, value in row.items()}
                    for row in result.mappings()
                ]
                rows.sort(key=_canonical_row)
                payload = json.dumps(
                    {"table": f"{table.schema}.{table.name}", "rows": rows},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                reads.append(_Read(table, rows, payload))
            conn.rollback()
    except SQLAlchemyError:
        # The driver's message can carry the host or role; the caller sees only the outcome.
        raise conflict("SOURCE_UNREACHABLE", "The source could not be reached.") from None
    finally:
        engine.dispose()
    return reads


def _external_id(row: dict[str, Any], primary_key: tuple[str, ...]) -> str:
    """The table's primary key, or a content identity when the table declares none.

    A keyless table has no natural identity; hashing the canonical row keeps a replay from
    duplicating it while still letting a changed row become a new observation.
    """
    values = [row.get(column) for column in primary_key]
    if primary_key and all(value is not None for value in values):
        if len(values) == 1:
            return str(values[0])
        return json.dumps(values, ensure_ascii=False, separators=(",", ":"))
    return "row:" + hashlib.sha256(_canonical_row(row).encode("utf-8")).hexdigest()


def _persist(
    conn: Connection, ctx: TenantContext, source: Any, reads: list[_Read]
) -> dict[str, Any]:
    objects = []
    total = 0
    for read in reads:
        raw = evidence.store_bytes(
            conn,
            tenant_id=ctx.tenant_id,
            source_id=source.id,
            data=read.payload,
            evidence_type="RECORD",
            content_type="application/json",
            retrieval_context=RetrievalContext(
                provenance="database",
                row_count=len(read.rows),
                request={
                    "method": "SELECT",
                    "path": f"{read.table.schema}.{read.table.name}",
                },
            ),
        )
        record_type = database_record_type(
            source.connector_id, read.table.schema, read.table.name
        )
        for row in read.rows:
            records.persist_record(
                conn,
                SourceRecordEnvelope(
                    id=uuid4(),
                    tenant_id=ctx.tenant_id,
                    source_id=source.id,
                    source_object_id=read.table.id,
                    external_id=_external_id(row, read.table.primary_key),
                    record_type=record_type,
                    attributes=row,
                    raw_evidence_id=raw.id,
                    occurred_at=None,
                    observed_at=datetime.now(UTC),
                    payload_hash=raw.content_hash,
                    connector_version=source.connector_version,
                    mapping_version=None,
                ),
            )
        total += len(read.rows)
        objects.append({"object_name": read.table.name, "row_count": len(read.rows)})
    write_audit(
        conn,
        ctx.tenant_id,
        ctx.principal,
        "source.synced",
        "source",
        {"source_id": str(source.id), "objects": objects, "record_count": total},
    )
    return {"source_id": str(source.id), "objects": objects, "record_count": total}


def sync(ctx: TenantContext, source_id: str) -> dict[str, Any]:
    try:
        source_uuid = UUID(source_id)
    except ValueError:
        raise not_found() from None
    with tenant_tx(ctx) as conn:
        source = _load_source(conn, source_uuid)
        if source.connector_id == "paypal":
            from app.connectors.paypal.sync import sync_paypal
            return sync_paypal(ctx, source)
        tables = _tables(conn, source_uuid, source.config or {})
    secret = resolve_secret(ctx.tenant_id, source.secret_reference)
    reads = _read(secret, tables)
    with tenant_tx(ctx) as conn:
        source = _load_source(conn, source_uuid)
        return _persist(conn, ctx, source, reads)
