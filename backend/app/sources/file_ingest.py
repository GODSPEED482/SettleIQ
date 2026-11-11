"""Bank CSV and Balance Reconciliation Report ingestion (spec 10.3.1, 7.1).

A provider report and the SYNTHETIC/REFERENCE fixture take the same path: the original bytes
are stored first, the file becomes one CSV source object named by its hash, and every data
row becomes one canonical record. Only the evidence provenance differs, and it stays on the
raw evidence so later stages can tell provider data from reference data.
"""

import hashlib
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import Connection, text

from app.audit import write_audit
from app.authz.guard import TenantContext
from app.connectors.manifest import load_connector_manifests
from app.db import tenant_tx
from app.errors import conflict, not_found
from app.sources import evidence, records
from app.sources.conventions import ensure_object, file_object_name, file_record_type
from app.sources.csv_files import read_csv
from app.sources.envelope import SourceRecordEnvelope
from app.sources.evidence import RetrievalContext

Kind = Literal["BANK_CSV", "BRR"]

# The columns every Balance Report needs; packs/finops/fixtures holds the SYNTHETIC/REFERENCE
# fixture built on this layout. Parsing is by column name, so additional columns are kept.
BRR_REQUIRED_COLUMNS = ("Transaction ID", "Date", "Type", "Currency", "Net", "Balance")
BALANCE_AFFECTING_COLUMN = "Balance Affecting"
_BALANCE_AFFECTING = {"Y", "YES", "TRUE", "1"}
_CURRENCY = re.compile(r"^[A-Z]{3}$")


def _load_source(conn: Connection, source_id: UUID) -> Any:
    row = conn.execute(
        text(
            """
            SELECT id, connector_id, connector_version, status
            FROM data_sources WHERE id = :id
            """
        ),
        {"id": source_id},
    ).first()
    if row is None:
        raise not_found()
    manifest = load_connector_manifests().get(row.connector_id)
    if manifest is None or not manifest.supports_file_ingestion:
        raise conflict("FILE_INGESTION_UNSUPPORTED", "This connector does not accept files.")
    if row.status != "ACTIVE":
        raise conflict("SOURCE_INACTIVE", "Only an active source can receive files.")
    return row


def _provenance(kind: Kind, filename: str) -> str:
    if kind == "BANK_CSV":
        return "tenant_file"
    return "synthetic_reference" if "SYNTHETIC" in filename.upper() else "provider_report"


def _decimal(value: str, row_number: int, column: str) -> Decimal:
    try:
        return Decimal(value.strip().replace(",", ""))
    except (InvalidOperation, ValueError):
        raise conflict(
            "BRR_ROW_INVALID", f"Row {row_number} has an invalid {column}."
        ) from None


def _validate_brr(header: list[str], rows: list[list[str]]) -> None:
    missing = [column for column in BRR_REQUIRED_COLUMNS if column not in header]
    if missing:
        raise conflict(
            "BRR_HEADER_INVALID", f"A Balance Report is missing {', '.join(missing)}."
        )
    if not rows:
        raise conflict("BRR_EMPTY", "A Balance Report needs at least one data row.")
    index = {column: position for position, column in enumerate(header)}
    affecting = index.get(BALANCE_AFFECTING_COLUMN)
    previous: dict[str, Decimal] = {}
    for number, row in enumerate(rows, start=1):
        for column in BRR_REQUIRED_COLUMNS:
            if not row[index[column]].strip():
                raise conflict(
                    "BRR_ROW_INVALID", f"Row {number} has no {column}."
                )
        currency = row[index["Currency"]].strip()
        if not _CURRENCY.fullmatch(currency):
            raise conflict("BRR_ROW_INVALID", f"Row {number} has an invalid Currency.")
        net = _decimal(row[index["Net"]], number, "Net")
        balance = _decimal(row[index["Balance"]], number, "Balance")
        if affecting is not None and row[affecting].strip().upper() not in _BALANCE_AFFECTING:
            continue
        if currency not in previous:
            previous[currency] = balance
            continue
        expected = previous[currency] + net
        if expected != balance:
            raise conflict(
                "BRR_TOTALS_INCONSISTENT", f"Row {number} does not continue the balance."
            )
        previous[currency] = balance


def _rows_as_attributes(header: list[str], rows: list[list[str]]) -> list[dict[str, str]]:
    return [
        {column: row[position] for position, column in enumerate(header)}
        for row in rows
    ]


def _persist(
    conn: Connection,
    ctx: TenantContext,
    source: Any,
    *,
    filename: str,
    kind: Kind,
    data: bytes,
    header: list[str],
    rows: list[list[str]],
) -> dict[str, Any]:
    digest = hashlib.sha256(data).hexdigest()
    provenance = _provenance(kind, filename)
    raw = evidence.store_bytes(
        conn,
        tenant_id=ctx.tenant_id,
        source_id=source.id,
        data=data,
        evidence_type="FILE" if kind == "BANK_CSV" else "REPORT",
        content_type="text/csv; charset=utf-8",
        retrieval_context=RetrievalContext(
            provenance=provenance, file_name=filename, row_count=len(rows)
        ),
    )
    object_name = file_object_name(filename, digest)
    object_id = ensure_object(
        conn,
        tenant_id=ctx.tenant_id,
        source_id=source.id,
        object_name=object_name,
        object_type="CSV",
        checksum=digest,
    )
    record_type = file_record_type(source.connector_id, kind)
    for number, attributes in enumerate(_rows_as_attributes(header, rows), start=1):
        records.persist_record(
            conn,
            SourceRecordEnvelope(
                id=uuid4(),
                tenant_id=ctx.tenant_id,
                source_id=source.id,
                source_object_id=object_id,
                external_id=f"row:{number}",
                record_type=record_type,
                attributes=attributes,
                raw_evidence_id=raw.id,
                occurred_at=None,
                observed_at=datetime.now(UTC),
                payload_hash=raw.content_hash,
                connector_version=source.connector_version,
                mapping_version=None,
            ),
        )
    write_audit(
        conn,
        ctx.tenant_id,
        ctx.principal,
        "source.file.uploaded",
        "source",
        {
            "source_id": str(source.id),
            "evidence_id": str(raw.id),
            "object_name": object_name,
            "record_count": len(rows),
            "provenance": provenance,
            "checksum": digest,
        },
    )
    return {
        "source_id": str(source.id),
        "evidence_id": str(raw.id),
        "object_name": object_name,
        "checksum": digest,
        "provenance": provenance,
        "record_count": len(rows),
    }


def ingest(ctx: TenantContext, source_id: str, data: bytes, filename: str, kind: str) -> dict:
    try:
        source_uuid = UUID(source_id)
    except ValueError:
        raise not_found() from None
    if kind not in ("BANK_CSV", "BRR"):
        raise conflict("FILE_KIND_INVALID", "A file is a bank CSV or a Balance Report CSV.")
    filename = Path(filename).name or "file"
    with tenant_tx(ctx) as conn:
        _load_source(conn, source_uuid)
    header, rows, _ = read_csv(data)
    if kind == "BRR":
        _validate_brr(header, rows)
    with tenant_tx(ctx) as conn:
        source = _load_source(conn, source_uuid)
        return _persist(
            conn,
            ctx,
            source,
            filename=filename,
            kind=kind,
            data=data,
            header=header,
            rows=rows,
        )
