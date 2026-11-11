"""PostgreSQL schema discovery and aggregate profiling (task 2.5, AC-ONT-01, AC-ONT-02).

The remote connection is read-only and bounded by a statement timeout. Only catalog metadata
and aggregates leave the tenant's database; no raw rows are stored.
"""

import logging
import re
from typing import Any

import psycopg
from psycopg import sql

from app.errors import ApiError
from app.sources.discovery_types import (
    FieldSnapshot,
    ForeignKeySnapshot,
    IndexSnapshot,
    ObjectProfile,
    ObjectResult,
    SchemaSnapshot,
)

log = logging.getLogger(__name__)

_NUMERIC = {"int2", "int4", "int8", "numeric", "float4", "float8"}
_TEMPORAL = {"date", "time", "timetz", "timestamp", "timestamptz"}
_TEXTUAL = {"text", "varchar", "bpchar", "uuid", "inet", "citext"}
_DISTINCTABLE = _NUMERIC | _TEMPORAL | _TEXTUAL | {"bool"}
_RANGEABLE = _NUMERIC | _TEMPORAL | {"bool", "text", "varchar", "bpchar", "citext"}
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_DISTRIBUTABLE = (
    "status", "state", "type", "kind", "category", "currency", "method", "provider",
    "country", "channel", "code",
)
_PRIVATE = (
    "email", "phone", "name", "address", "account", "iban", "card", "ssn", "customer",
    "postal", "zip", "birth", "dob", "tax_id", "national", "passport",
)
_MONETARY = (
    "amount", "total", "price", "fee", "tax", "subtotal", "discount", "shipping", "balance",
)


def discover(
    dsn: str, schema: str, timeout_seconds: int
) -> list[ObjectResult]:
    """Discover one schema over a read-only connection, in a consistent snapshot."""
    try:
        conn = psycopg.connect(dsn, connect_timeout=timeout_seconds)
    except psycopg.Error as error:
        log.warning("source discovery connection failed: %s", type(error).__name__)
        raise _unavailable("The source database could not be reached.") from None
    try:
        with conn.transaction():
            conn.execute("SET TRANSACTION READ ONLY")
            conn.execute(
                sql.SQL("SET LOCAL statement_timeout = {}").format(
                    sql.Literal(timeout_seconds * 1000)
                )
            )
            conn.execute("SET LOCAL TimeZone = 'UTC'")
            if conn.execute("SHOW transaction_read_only").fetchone()[0] != "on":
                raise _unavailable("The source connection could not be made read-only.")
            return _discover(conn, schema)
    except psycopg.Error as error:
        log.warning("source discovery failed: %s", type(error).__name__)
        raise _unavailable("The source database could not be profiled.") from None
    finally:
        conn.close()


def _unavailable(message: str) -> ApiError:
    return ApiError(502, "SOURCE_UNAVAILABLE", message)


def _discover(conn: psycopg.Connection, schema: str) -> list[ObjectResult]:
    objects = conn.execute(
        """
        SELECT c.relname, c.relkind
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s AND c.relkind IN ('r', 'p', 'v', 'm')
        ORDER BY c.relname
        """,
        (schema,),
    ).fetchall()
    return [_discover_object(conn, schema, name, relkind) for name, relkind in objects]


def _discover_object(
    conn: psycopg.Connection, schema: str, table: str, relkind: str
) -> ObjectResult:
    object_type = "TABLE" if relkind in ("r", "p") else "VIEW"
    columns = _columns(conn, schema, table)
    constraints = _constraints(conn, schema, table)
    indexes = _indexes(conn, schema, table)
    primary_key = next((columns_at for kind, columns_at in constraints if kind == "p"), [])
    unique_constraints = [columns_at for kind, columns_at in constraints if kind == "u"]
    unique_columns = {item[0] for item in unique_constraints if len(item) == 1}
    unique_columns |= {
        columns_at[0]
        for _, columns_at, unique, _, partial in indexes
        if unique and not partial and len(columns_at) == 1
    }
    if len(primary_key) == 1:
        unique_columns.add(primary_key[0])

    snapshot = SchemaSnapshot(
        object_type=object_type,
        fields=[
            FieldSnapshot(
                name=column["name"],
                datatype=column["datatype"],
                nullable=column["nullable"],
                unique=column["name"] in unique_columns,
            )
            for column in columns
        ],
        primary_key=primary_key,
        unique_constraints=unique_constraints,
        foreign_keys=[
            ForeignKeySnapshot(field=field, references=reference)
            for field, reference in _foreign_keys(conn, schema, table)
        ],
        indexes=[
            IndexSnapshot(name=name, columns=columns_at, unique=unique, primary=primary)
            for name, columns_at, unique, primary, _ in indexes
        ],
        format={"schema": schema},
    )
    return ObjectResult(
        object_name=f"{schema}.{table}",
        snapshot=snapshot,
        profile=_profile(conn, schema, table, columns),
    )


def _columns(conn: psycopg.Connection, schema: str, table: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT a.attname, format_type(a.atttypid, a.atttypmod), t.typname, t.typtype,
            NOT a.attnotnull
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_type t ON t.oid = a.atttypid
        WHERE n.nspname = %s AND c.relname = %s AND a.attnum > 0 AND NOT a.attisdropped
        ORDER BY a.attnum
        """,
        (schema, table),
    ).fetchall()
    return [
        {"name": name, "datatype": datatype, "typname": typname, "typtype": typtype,
         "nullable": nullable}
        for name, datatype, typname, typtype, nullable in rows
    ]


def _constraints(
    conn: psycopg.Connection, schema: str, table: str
) -> list[tuple[str, list[str]]]:
    return conn.execute(
        """
        SELECT con.contype, array_agg(att.attname ORDER BY k.ord)
        FROM pg_constraint con
        JOIN pg_class c ON c.oid = con.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        CROSS JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord)
        JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = k.attnum
        WHERE n.nspname = %s AND c.relname = %s AND con.contype IN ('p', 'u')
        GROUP BY con.contype, con.conname
        ORDER BY con.conname
        """,
        (schema, table),
    ).fetchall()


def _foreign_keys(
    conn: psycopg.Connection, schema: str, table: str
) -> list[tuple[str, str]]:
    rows = conn.execute(
        """
        SELECT att.attname, fn.nspname, fc.relname, fatt.attname
        FROM pg_constraint con
        JOIN pg_class c ON c.oid = con.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        CROSS JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord)
        JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = k.attnum
        JOIN LATERAL unnest(con.confkey) WITH ORDINALITY AS fk(attnum, ord) ON fk.ord = k.ord
        JOIN pg_class fc ON fc.oid = con.confrelid
        JOIN pg_namespace fn ON fn.oid = fc.relnamespace
        JOIN pg_attribute fatt ON fatt.attrelid = con.confrelid AND fatt.attnum = fk.attnum
        WHERE n.nspname = %s AND c.relname = %s AND con.contype = 'f'
        ORDER BY con.conname, k.ord
        """,
        (schema, table),
    ).fetchall()
    return [(field, f"{ref_schema}.{ref_table}.{ref_field}")
            for field, ref_schema, ref_table, ref_field in rows]


def _indexes(
    conn: psycopg.Connection, schema: str, table: str
) -> list[tuple[str, list[str], bool, bool, bool]]:
    return conn.execute(
        """
        SELECT i.relname, array_agg(COALESCE(a.attname, '<expression>') ORDER BY k.ord),
            ix.indisunique, ix.indisprimary, ix.indpred IS NOT NULL
        FROM pg_index ix
        JOIN pg_class i ON i.oid = ix.indexrelid
        JOIN pg_class c ON c.oid = ix.indrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        CROSS JOIN LATERAL unnest(ix.indkey) WITH ORDINALITY AS k(attnum, ord)
        LEFT JOIN pg_attribute a ON a.attrelid = ix.indrelid AND a.attnum = k.attnum
        WHERE n.nspname = %s AND c.relname = %s
        GROUP BY i.relname, ix.indisunique, ix.indisprimary, ix.indpred
        ORDER BY i.relname
        """,
        (schema, table),
    ).fetchall()


def _profile(
    conn: psycopg.Connection, schema: str, table: str, columns: list[dict[str, Any]]
) -> ObjectProfile:
    ident = sql.Identifier(schema)
    relation = sql.Identifier(table)
    selects = [sql.SQL("count(*)")]
    positions: dict[str, dict[str, int]] = {}
    for column in columns:
        positions[column["name"]] = {}
        name = sql.Identifier(column["name"])
        start = len(selects)
        selects.append(
            sql.SQL("count({})").format(name)
        )
        if _distinctable(column):
            selects.append(sql.SQL("count(DISTINCT {})").format(name))
        if _rangeable(column):
            selects.append(sql.SQL("min({})::text").format(name))
            selects.append(sql.SQL("max({})::text").format(name))
        if _base_type(column) == "bool":
            selects.append(sql.SQL("count(*) FILTER (WHERE {})").format(name))
            selects.append(sql.SQL("count(*) FILTER (WHERE NOT {})").format(name))
        positions[column["name"]]["start"] = start

    query = sql.SQL("SELECT {} FROM {}.{}").format(
        sql.SQL(", ").join(selects), ident, relation
    )
    row = conn.execute(query).fetchone()
    row_count = row[0]
    fields: dict[str, dict[str, Any]] = {}
    currency_columns = [
        column["name"]
        for column in columns
        if ("currency" in column["name"].lower() or column["name"].lower() == "ccy")
        and _base_type(column) in _TEXTUAL
    ]

    for column in columns:
        name = column["name"]
        start = positions[name]["start"]
        non_null = row[start]
        profile: dict[str, Any] = {
            "non_null_count": non_null,
            "null_count": row_count - non_null,
            "null_fraction": round((row_count - non_null) / row_count, 6) if row_count else 0.0,
        }
        cursor = start + 1
        if _distinctable(column):
            distinct_count = row[cursor]
            profile["distinct_count"] = distinct_count
            profile["duplicate_count"] = non_null - distinct_count
            cursor += 1
        if _rangeable(column):
            profile["min"] = row[cursor]
            profile["max"] = row[cursor + 1]
            cursor += 2
        if _base_type(column) == "bool":
            profile["true_count"] = row[cursor]
            profile["false_count"] = row[cursor + 1]
            profile["distribution"] = [
                {"value": "true", "count": row[cursor]},
                {"value": "false", "count": row[cursor + 1]},
            ]
        column_ident = sql.Identifier(name)
        if _distributable(name) and (
            _base_type(column) in _TEXTUAL or column["typtype"] == "e"
        ):
            profile["distribution"] = _distribution(conn, ident, relation, column_ident)
        elif _base_type(column) in _TEMPORAL - {"time", "timetz"}:
            profile["distribution"] = _temporal_distribution(
                conn, ident, relation, column_ident
            )
        if "distribution" in profile and _is_currency_name(name):
            values = [item["value"] for item in profile["distribution"]]
            if values and all(_CURRENCY.fullmatch(value) for value in values):
                profile["currency"] = True
        fields[name] = profile

    for column in columns:
        if not _is_monetary_name(column["name"]) or _base_type(column) not in _NUMERIC:
            continue
        fields[column["name"]]["monetary"] = True
        if currency_columns:
            fields[column["name"]]["currency_column"] = currency_columns[0]
    return ObjectProfile(row_count=row_count, sampled=False, fields=fields)


def _distribution(
    conn: psycopg.Connection, schema: sql.Identifier, relation: sql.Identifier,
    column: sql.Identifier
) -> list[dict[str, Any]]:
    query = sql.SQL(
        "SELECT btrim({column}::text), count(*) FROM {schema}.{relation} "
        "WHERE {column} IS NOT NULL GROUP BY {column} "
        "ORDER BY count(*) DESC, {column} LIMIT 20"
    ).format(column=column, schema=schema, relation=relation)
    return [{"value": value, "count": count} for value, count in conn.execute(query).fetchall()]


def _temporal_distribution(
    conn: psycopg.Connection, schema: sql.Identifier, relation: sql.Identifier,
    column: sql.Identifier
) -> dict[str, Any]:
    query = sql.SQL(
        "SELECT date_trunc('day', {column})::date::text, count(*) FROM {schema}.{relation} "
        "WHERE {column} IS NOT NULL GROUP BY 1 ORDER BY 1 DESC LIMIT 100"
    ).format(column=column, schema=schema, relation=relation)
    rows = list(reversed(conn.execute(query).fetchall()))
    return {
        "bucket": "day",
        "values": [{"value": value, "count": count} for value, count in rows],
        "truncated": len(rows) == 100,
    }


def _base_type(column: dict[str, Any]) -> str:
    return column["typname"]


def _distinctable(column: dict[str, Any]) -> bool:
    return _base_type(column) in _DISTINCTABLE or column["typtype"] == "e"


def _rangeable(column: dict[str, Any]) -> bool:
    return _base_type(column) in _RANGEABLE or column["typtype"] == "e"


def _distributable(name: str) -> bool:
    lowered = name.lower()
    return any(hint in lowered for hint in _DISTRIBUTABLE) and not any(
        hint in lowered for hint in _PRIVATE
    )


def _is_currency_name(name: str) -> bool:
    lowered = name.lower()
    return "currency" in lowered or lowered == "ccy"


def _is_monetary_name(name: str) -> bool:
    lowered = name.lower()
    return any(hint in lowered for hint in _MONETARY)
