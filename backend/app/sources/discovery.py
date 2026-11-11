"""Source discovery and profiling (spec 5.7, 14.2 step 4, AC-ONT-01, AC-ONT-02).

Discovery writes what a source holds to `source_objects`: its structure as `schema_snapshot`
and profile aggregates as `profile`. No raw row and no secret is stored, logged or audited.
Adapters only describe structure; deciding what a field means is generation's job (spec 4.1).

Only a connector whose manifest declares `supports_discovery` is discovered. A PostgreSQL
source is read through its own read-only connection, opened outside any tenant transaction,
so a slow source never holds a platform transaction open. A CSV source is discovered from the
stored raw-evidence copy of each file, checked against its hash, never from an upload.
"""

import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, inspect, text
from sqlalchemy.exc import SQLAlchemyError

from app.audit import write_audit
from app.authz.guard import TenantContext
from app.connectors.manifest import load_connector_manifests
from app.db import tenant_tx
from app.errors import conflict, not_found
from app.sources import evidence, profiling
from app.sources.conventions import ObjectType, file_object_name
from app.sources.csv_files import CSV_CONTENT_TYPES, read_csv
from app.sources.postgres import read_only_engine
from app.sources.profiling import ColumnSpec
from app.sources.secrets import resolve_secret


@dataclass(frozen=True)
class DiscoveredObject:
    name: str
    object_type: ObjectType
    schema_snapshot: dict[str, Any]
    profile: dict[str, Any]
    checksum: str | None = None


def _load_source(conn: Connection, source_id: UUID) -> Any:
    row = conn.execute(
        text(
            """
            SELECT id, connector_id, protocol, config, secret_reference, status
            FROM data_sources WHERE id = :id
            """
        ),
        {"id": source_id},
    ).first()
    if row is None:
        raise not_found()
    manifest = load_connector_manifests().get(row.connector_id)
    if manifest is None or not manifest.supports_discovery:
        raise conflict("DISCOVERY_UNSUPPORTED", "This connector does not support discovery.")
    if row.status != "ACTIVE":
        raise conflict("SOURCE_INACTIVE", "Only an active source can be discovered.")
    return row


def discover(ctx: TenantContext, source_id: str) -> dict[str, Any]:
    try:
        source_uuid = UUID(source_id)
    except ValueError:
        raise not_found() from None
    with tenant_tx(ctx) as conn:
        source = _load_source(conn, source_uuid)
    if source.protocol == "csv":
        with tenant_tx(ctx) as conn:
            _load_source(conn, source_uuid)
            return _record(conn, ctx, source_uuid, discover_csv(conn, source_uuid))
    if source.protocol == "postgresql":
        objects = discover_postgres(resolve_secret(source.secret_reference), source.config)
    else:
        raise conflict("DISCOVERY_UNSUPPORTED", "This connector does not support discovery.")
    with tenant_tx(ctx) as conn:
        _load_source(conn, source_uuid)
        return _record(conn, ctx, source_uuid, objects)


def _record(
    conn: Connection, ctx: TenantContext, source_id: UUID, objects: list[DiscoveredObject]
) -> dict[str, Any]:
    stored = [_upsert(conn, ctx.tenant_id, source_id, item) for item in objects]
    write_audit(
        conn, ctx.tenant_id, ctx.principal, "source.discovered", "source",
        {"source_id": str(source_id), "objects": [item["object_name"] for item in stored]},
    )
    return {"source_id": str(source_id), "objects": stored}


def _upsert(
    conn: Connection, tenant_id: UUID, source_id: UUID, item: DiscoveredObject
) -> dict[str, Any]:
    row = conn.execute(
        text(
            """
            INSERT INTO source_objects (id, tenant_id, source_id, object_name, object_type,
                schema_snapshot, profile, checksum)
            VALUES (:id, :tenant, :source, :name, :type, CAST(:snapshot AS jsonb),
                CAST(:profile AS jsonb), :checksum)
            ON CONFLICT (tenant_id, source_id, object_name) DO UPDATE
                SET schema_snapshot = EXCLUDED.schema_snapshot, profile = EXCLUDED.profile,
                    checksum = EXCLUDED.checksum, discovered_at = now()
                WHERE source_objects.object_type = EXCLUDED.object_type
            RETURNING object_name, object_type, discovered_at
            """
        ),
        {
            "id": uuid4(),
            "tenant": tenant_id,
            "source": source_id,
            "name": item.name,
            "type": item.object_type,
            "snapshot": json.dumps(item.schema_snapshot),
            "profile": json.dumps(item.profile),
            "checksum": item.checksum,
        },
    ).first()
    if row is None:
        raise conflict(
            "OBJECT_TYPE_CHANGED", f"{item.name} was discovered earlier as another object type."
        )
    return {
        "object_name": row.object_name,
        "object_type": row.object_type,
        "discovered_at": row.discovered_at.isoformat(),
        "row_count": item.profile.get("row_count"),
        "columns": len(item.schema_snapshot.get("columns", [])),
    }


# PostgreSQL


def _generic_datatype(column_type: Any) -> str:
    try:
        python_type = column_type.python_type
    except NotImplementedError:
        return "other"
    if python_type is bool:
        return "boolean"
    if python_type is int:
        return "integer"
    if python_type in (Decimal, float):
        return "decimal"
    if python_type is datetime:
        return "datetime"
    if python_type is date:
        return "date"
    if python_type is str:
        return "string"
    return "other"


def _native_type(column_type: Any, conn: Connection) -> str:
    try:
        return column_type.compile(dialect=conn.dialect)
    except Exception:  # noqa: BLE001 - an uncompilable type is still a column
        return type(column_type).__name__


def _table_snapshot(conn: Connection, schema: str, name: str) -> dict[str, Any]:
    inspector = inspect(conn)
    columns = inspector.get_columns(name, schema)
    primary_key = inspector.get_pk_constraint(name, schema).get("constrained_columns") or []
    foreign_keys = inspector.get_foreign_keys(name, schema)
    uniques = inspector.get_unique_constraints(name, schema)
    indexes = inspector.get_indexes(name, schema)
    single_unique = {tuple(primary_key)} | {
        tuple(item["column_names"]) for item in [*uniques, *(i for i in indexes if i["unique"])]
    }
    return {
        "schema": schema,
        "columns": [
            {
                "name": column["name"],
                "type": _native_type(column["type"], conn),
                "datatype": _generic_datatype(column["type"]),
                "nullable": bool(column["nullable"]),
                "primary_key": column["name"] in primary_key,
                "unique": (column["name"],) in single_unique,
            }
            for column in columns
        ],
        "primary_key": primary_key,
        "foreign_keys": [
            {
                "name": key.get("name"),
                "columns": key["constrained_columns"],
                "references": {
                    "schema": key.get("referred_schema") or schema,
                    "table": key["referred_table"],
                    "columns": key["referred_columns"],
                },
            }
            for key in foreign_keys
        ],
        "unique_constraints": [
            {"name": item.get("name"), "columns": item["column_names"]} for item in uniques
        ],
        "indexes": [
            {"name": item.get("name"), "columns": item["column_names"], "unique": item["unique"]}
            for item in indexes
        ],
    }


def _column_specs(snapshot: dict[str, Any]) -> list[ColumnSpec]:
    foreign = {column for key in snapshot["foreign_keys"] for column in key["columns"]}
    return [
        ColumnSpec(
            column["name"],
            column["datatype"],
            identifier=column["primary_key"]
            or column["unique"]
            or column["name"] in foreign
            or profiling.looks_like_identifier(column["name"]),
        )
        for column in snapshot["columns"]
    ]


def _profile_table(conn: Connection, schema: str, name: str, snapshot: dict[str, Any]) -> dict:
    quote = conn.dialect.identifier_preparer.quote
    relation = f"{quote(schema)}.{quote(name)}"
    columns = ", ".join(quote(column["name"]) for column in snapshot["columns"])
    row_count = conn.execute(text(f"SELECT count(*) FROM {relation}")).scalar_one()
    rows = conn.execute(
        text(f"SELECT {columns} FROM {relation} LIMIT :limit"), {"limit": profiling.SAMPLE_ROWS}
    )
    return profiling.profile(_column_specs(snapshot), rows, row_count=row_count)


def discover_postgres(secret: str, config: dict[str, Any]) -> list[DiscoveredObject]:
    """Tables and views of one schema, read in a read-only transaction."""
    schema = config.get("schema") or "public"
    engine = read_only_engine(secret)
    try:
        with engine.connect() as conn:
            inspector = inspect(conn)
            relations: list[tuple[str, ObjectType]] = [
                *((name, "TABLE") for name in inspector.get_table_names(schema)),
                *((name, "VIEW") for name in inspector.get_view_names(schema)),
            ]
            discovered = []
            for name, object_type in relations:
                snapshot = _table_snapshot(conn, schema, name)
                discovered.append(
                    DiscoveredObject(
                        name, object_type, snapshot, _profile_table(conn, schema, name, snapshot)
                    )
                )
            conn.rollback()
    except SQLAlchemyError:
        # The driver's message can carry the host or role; the caller sees only the outcome.
        raise conflict("SOURCE_UNREACHABLE", "The source could not be reached.") from None
    finally:
        engine.dispose()
    return discovered


# CSV files


def _csv_object(name: str, content_hash: str, size: int, data: bytes) -> DiscoveredObject:
    header, rows, delimiter = read_csv(data)
    sample = rows[: profiling.SAMPLE_ROWS]
    datatypes = [profiling.infer_datatype(row[i] for row in sample) for i in range(len(header))]
    typed = [
        [profiling.coerce(cell, datatype) for cell, datatype in zip(row, datatypes, strict=True)]
        for row in sample
    ]
    specs = [
        ColumnSpec(column, datatype, identifier=profiling.looks_like_identifier(column))
        for column, datatype in zip(header, datatypes, strict=True)
    ]
    profile = profiling.profile(specs, typed, row_count=len(rows))
    columns = []
    for index, column in enumerate(header):
        values = [row[index] for row in typed]
        present = [value for value in values if value is not None]
        columns.append(
            {
                "name": column,
                "type": "text",
                "datatype": datatypes[index],
                "nullable": len(present) < len(values),
                "primary_key": False,
                "unique": bool(present) and len(set(present)) == len(values),
            }
        )
    snapshot = {
        "columns": columns,
        "primary_key": [],
        "foreign_keys": [],
        "unique_constraints": [],
        "indexes": [],
        # A file declares no keys; uniqueness and nullability are what the sample shows.
        "inferred_from": "sample",
        "file": {
            "name": name,
            "content_hash": content_hash,
            "byte_size": size,
            "delimiter": delimiter,
        },
    }
    return DiscoveredObject(
        file_object_name(name, content_hash), "CSV", snapshot, profile, checksum=content_hash
    )


def discover_csv(conn: Connection, source_id: UUID) -> list[DiscoveredObject]:
    """One CSV object per distinct stored file (app/sources/conventions.py)."""
    files = conn.execute(
        text(
            """
            SELECT id, content_hash, byte_size, retrieval_context FROM raw_evidence
            WHERE source_id = :source AND evidence_type IN ('FILE', 'REPORT')
              AND split_part(lower(content_type), ';', 1) = ANY(:types)
            ORDER BY received_at, id
            """
        ),
        {"source": source_id, "types": list(CSV_CONTENT_TYPES)},
    ).all()
    if not files:
        raise conflict("NO_SOURCE_FILES", "Upload a file to this source before discovering it.")
    return [
        _csv_object(
            (item.retrieval_context or {}).get("file_name") or "file",
            item.content_hash,
            item.byte_size,
            evidence.read_verified(conn, item.id),
        )
        for item in files
    ]
