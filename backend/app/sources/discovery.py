"""Source discovery orchestration (tasks 2.5 and 2.6, AC-ONT-01, AC-ONT-02).

Discovery reads a PostgreSQL catalog or an uploaded CSV, stores structure under
`source_objects.schema_snapshot` and aggregate-only profiling under `source_objects.profile`,
and audits the run. Remote reads happen outside the tenant transaction; persistence and the
audit row commit together in the caller's tenant.
"""

import json
import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from sqlalchemy import Connection, text

from app.audit import write_audit
from app.authz.guard import TenantContext
from app.config import get_settings
from app.connectors.manifest import load_connector_manifests, require_capabilities
from app.db import tenant_tx
from app.errors import conflict, not_found, validation_failed
from app.sources import csv_discovery, evidence, postgres_discovery
from app.sources.discovery_types import (
    DiscoveryResult,
    ObjectResult,
    ObjectSummary,
)

_ENV = re.compile(r"^env:([A-Za-z_][A-Za-z0-9_]*)$")


@dataclass(frozen=True)
class _Source:
    id: UUID
    connector_id: str
    protocol: str
    status: str
    config: dict[str, Any]
    secret_reference: str | None
    capabilities: list[dict[str, Any]]


def discover_source(ctx: TenantContext, raw_source_id: str) -> DiscoveryResult:
    try:
        source_id = UUID(raw_source_id)
    except ValueError:
        raise not_found() from None
    source = _load_source(ctx, source_id)
    if source.status != "ACTIVE":
        raise conflict("SOURCE_DISABLED", "This source is disabled.")
    manifest = load_connector_manifests().get(source.connector_id)
    if manifest is None:
        raise validation_failed("Unknown connector.")

    if source.protocol == "postgresql":
        _require_capability(source, manifest.supports_discovery)
        results = _discover_postgres(source)
        kind = "postgresql"
    elif source.protocol == "csv":
        if not manifest.supports_file_ingestion:
            raise validation_failed("This connector does not support file discovery.")
        results = [_discover_csv(ctx, source)]
        kind = "csv"
    else:
        raise validation_failed("This source protocol does not support discovery.")

    with tenant_tx(ctx) as conn:
        status = conn.execute(
            text("SELECT status FROM data_sources WHERE id = :id"),
            {"id": source.id},
        ).scalar_one_or_none()
        if status != "ACTIVE":
            raise conflict("SOURCE_DISABLED", "This source is disabled.")
        for result in results:
            checksum = (
                result.snapshot.format.get("content_hash")
                if result.snapshot.object_type == "CSV"
                else None
            )
            _store(conn, ctx.tenant_id, source, result, checksum)
        write_audit(
            conn, ctx.tenant_id, ctx.principal, "source.discovered", "source",
            {"source_id": str(source.id), "kind": kind, "objects": len(results)},
        )
    return DiscoveryResult(
        source_id=str(source.id),
        kind=kind,
        objects=[
            ObjectSummary(
                object=result.object_name,
                object_type=result.snapshot.object_type,
                fields=len(result.snapshot.fields),
            )
            for result in results
        ],
    )


def _load_source(ctx: TenantContext, source_id: UUID) -> _Source:
    with tenant_tx(ctx) as conn:
        row = conn.execute(
            text(
                """
                SELECT id, connector_id, protocol, status, config, secret_reference,
                    capabilities
                FROM data_sources WHERE id = :id
                """
            ),
            {"id": source_id},
        ).first()
    if row is None:
        raise not_found()
    return _Source(
        id=row.id,
        connector_id=row.connector_id,
        protocol=row.protocol,
        status=row.status,
        config=row.config or {},
        secret_reference=row.secret_reference,
        capabilities=row.capabilities or [],
    )


def _require_capability(source: _Source, supports_discovery: bool) -> None:
    if not supports_discovery:
        raise validation_failed("This connector does not support discovery.")
    try:
        require_capabilities(source.capabilities, ["database.schema.read"])
    except ValueError as error:
        raise validation_failed(str(error)) from None


def _discover_postgres(source: _Source) -> list[ObjectResult]:
    schema = source.config.get("schema", "public")
    if not isinstance(schema, str) or not schema or len(schema) > 63:
        raise validation_failed("The source's schema configuration is invalid.")
    dsn = _resolve_secret(source.secret_reference)
    return postgres_discovery.discover(
        dsn, schema, get_settings().source_discovery_timeout_seconds
    )


def _discover_csv(ctx: TenantContext, source: _Source) -> ObjectResult:
    with tenant_tx(ctx) as conn:
        row = conn.execute(
            text(
                """
                SELECT content_hash, storage_key, retrieval_context
                FROM raw_evidence
                WHERE source_id = :source AND evidence_type IN ('FILE', 'REPORT')
                ORDER BY received_at DESC, id DESC
                LIMIT 1
                """
            ),
            {"source": source.id},
        ).first()
    if row is None:
        raise conflict("NO_EVIDENCE", "No uploaded file is available to discover.")
    try:
        data = evidence.read_bytes(row.storage_key)
    except (OSError, ValueError):
        raise conflict("EVIDENCE_UNAVAILABLE", "The stored file could not be read.") from None
    file_name = (row.retrieval_context or {}).get("file_name") or "upload.csv"
    return csv_discovery.discover(
        data, file_name, row.content_hash, get_settings().source_discovery_max_csv_rows
    )


def _resolve_secret(reference: str | None) -> str:
    if reference is None:
        raise validation_failed("This source has no database secret reference.")
    match = _ENV.fullmatch(reference)
    if match is None:
        raise validation_failed("Only env:<NAME> database secret references are supported.")
    value = os.environ.get(match.group(1))
    if not value:
        raise validation_failed("The referenced database secret is not configured.")
    dsn = value.replace("postgresql+psycopg://", "postgresql://", 1)
    if dsn.startswith("postgres://"):
        dsn = "postgresql://" + dsn[len("postgres://") :]
    if urlsplit(dsn).scheme != "postgresql":
        raise validation_failed("The referenced database secret is not a PostgreSQL DSN.")
    return dsn


def _store(
    conn: Connection,
    tenant_id: UUID,
    source: _Source,
    result: ObjectResult,
    checksum: str | None,
) -> None:
    conn.execute(
        text(
            """
            INSERT INTO source_objects (id, tenant_id, source_id, object_name, object_type,
                schema_snapshot, profile, checksum, discovered_at)
            VALUES (:id, :tenant, :source, :name, :type,
                CAST(:snapshot AS jsonb), CAST(:profile AS jsonb), :checksum, now())
            ON CONFLICT (tenant_id, source_id, object_name) DO UPDATE SET
                object_type = EXCLUDED.object_type,
                schema_snapshot = EXCLUDED.schema_snapshot,
                profile = EXCLUDED.profile,
                checksum = EXCLUDED.checksum,
                discovered_at = now()
            """
        ),
        {
            "id": uuid4(),
            "tenant": tenant_id,
            "source": source.id,
            "name": result.object_name,
            "type": result.snapshot.object_type,
            "snapshot": json.dumps(result.snapshot.model_dump(mode="json")),
            "profile": json.dumps(result.profile.model_dump(mode="json")),
            "checksum": checksum,
        },
    )
