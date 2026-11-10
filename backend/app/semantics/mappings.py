"""Versioned source bindings and fact-specific authority assertions (spec 5.4, 5.6, ARCH-07).

Several bindings may target one property, but only when their values are truly comparable.
Bindings and assertions are append-only and belong to a draft until it is published.
"""

import json
from collections import defaultdict
from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import Connection, text

from app.audit import write_audit
from app.authz.guard import TenantContext
from app.db import tenant_tx
from app.errors import conflict, not_found, validation_failed

TERM =r"^[a-z][a-z0-9_]*:[A-Za-z][A-Za-z0-9_]*$"


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Transform(_Body):
    """A fixed allowlist. Mappings never carry code, SQL or expressions."""

    name: Literal["identity", "decimal_string", "upper", "lower", "iso8601", "enum_map"]
    values: dict[str, str] | None = None

    @model_validator(mode="after")
    def _values_only_for_enum_map(self) -> "Transform":
        if (self.name == "enum_map") != (self.values is not None):
            raise ValueError("values are required for enum_map and allowed only there")
        return self


class MappingBody(_Body):
    draft_id: UUID
    source_id: UUID
    external_object: str = Field(min_length=1, max_length=300)
    field_path: str = Field(min_length=1, max_length=500)
    property_iri: str = Field(pattern=TERM)
    class_iri: str = Field(pattern=TERM)
    datatype: Literal["decimal", "string", "datetime", "boolean", "integer"]
    transform: Transform
    nullable: bool
    is_unique: bool
    sync_strategy: Literal["batch", "event", "file"]
    value_basis: str = Field(min_length=1, max_length=100)
    unit: str | None = Field(default=None, max_length=50)
    currency_field: str | None = Field(default=None, max_length=500)
    temporal_context: str = Field(min_length=1, max_length=100)
    method: Literal["human", "accepted_proposal"]


class AuthorityBody(_Body):
    draft_id: UUID
    property_iri: str = Field(pattern=TERM)
    entity_type_iri: str = Field(pattern=TERM)
    source_id: UUID
    valid_from: AwareDatetime
    valid_to: AwareDatetime | None = None
    verification_method: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def _interval(self) -> "AuthorityBody":
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError("valid_to must be after valid_from")
        return self


_MAPPING_FIELDS = (
    "source_id", "external_object", "field_path", "property_iri", "class_iri", "datatype",
    "transform", "nullable", "is_unique", "sync_strategy", "value_basis", "unit",
    "currency_field", "temporal_context", "method",
)
_MAPPING_KEY = "source_id, external_object, field_path, property_iri"
_AUTHORITY_FIELDS = (
    "property_iri", "entity_type_iri", "source_id", "valid_from", "valid_to",
    "verification_method",
)
_AUTHORITY_KEY = "property_iri, entity_type_iri, source_id"


def _lock_open_draft(conn: Connection, draft_id: UUID) -> None:
    row = conn.execute(
        text("SELECT status FROM ontology_drafts WHERE id = :id FOR UPDATE"), {"id": draft_id}
    ).first()
    if row is None:
        raise not_found()
    if row.status != "OPEN":
        raise conflict("STALE_DRAFT", "This draft is no longer open.")


def _require_source(conn: Connection, source_id: UUID) -> None:
    """RLS hides other tenants' sources, so they get the same answer as a missing one."""
    found = conn.execute(
        text("SELECT 1 FROM data_sources WHERE id = :id"), {"id": source_id}
    ).first()
    if found is None:
        raise validation_failed("Unknown source.")


def _json_safe(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _current(
    conn: Connection, table: str, key: str, fields: tuple[str, ...], draft_id: UUID
) -> list[dict[str, Any]]:
    """The newest version of each key, leaving out retired ones, in a stable order."""
    columns = ", ".join(fields)
    rows = conn.execute(
        text(
            f"""
            SELECT id, version, {columns} FROM (
                SELECT DISTINCT ON ({key}) * FROM {table}
                WHERE draft_id = :draft
                ORDER BY {key}, version DESC
            ) latest
            WHERE status = 'ACTIVE'
            ORDER BY {key}
            """
        ),
        {"draft": draft_id},
    ).all()
    return [{name: _json_safe(value) for name, value in row._mapping.items()} for row in rows]


def current_mappings(conn: Connection, draft_id: UUID) -> list[dict[str, Any]]:
    return _current(conn, "source_mappings", _MAPPING_KEY, _MAPPING_FIELDS, draft_id)


def current_authority(conn: Connection, draft_id: UUID) -> list[dict[str, Any]]:
    return _current(conn, "authority_assertions", _AUTHORITY_KEY, _AUTHORITY_FIELDS, draft_id)


def hashable(rows: list[dict[str, Any]], fields: tuple[str, ...]) -> list[dict[str, Any]]:
    """Semantic content only, so a row's ID or version number cannot change the hash."""
    return [{name: row[name] for name in fields} for row in rows]


def candidate_mappings(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return hashable(rows, _MAPPING_FIELDS)


def candidate_authority(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return hashable(rows, _AUTHORITY_FIELDS)


_INSERT_MAPPING = text(
    """
    INSERT INTO source_mappings (id, tenant_id, draft_id, source_id, external_object, field_path,
        property_iri, class_iri, version, status, datatype, transform, nullable, is_unique,
        sync_strategy, value_basis, unit, currency_field, temporal_context, method, created_by)
    VALUES (:id, :tenant, :draft, :source_id, :external_object, :field_path, :property_iri,
        :class_iri,
        (SELECT COALESCE(max(version), 0) + 1 FROM source_mappings
         WHERE draft_id = :draft AND source_id = :source_id
           AND external_object = :external_object AND field_path = :field_path
           AND property_iri = :property_iri),
        :status, :datatype, CAST(:transform AS jsonb), :nullable, :is_unique, :sync_strategy,
        :value_basis, :unit, :currency_field, :temporal_context, :method, :user)
    RETURNING id, version
    """
)


def _audit(conn: Connection, ctx: TenantContext, action: str, resource: str, detail: dict) -> None:
    write_audit(conn, ctx.tenant_id, ctx.principal, action, resource, detail)


def save_mapping(ctx: TenantContext, body: MappingBody) -> dict[str, Any]:
    values = body.model_dump(exclude={"transform", "draft_id"})
    with tenant_tx(ctx) as conn:
        _lock_open_draft(conn, body.draft_id)
        _require_source(conn, body.source_id)
        row = conn.execute(
            _INSERT_MAPPING,
            values
            | {
                "id": uuid4(),
                "tenant": ctx.tenant_id,
                "draft": body.draft_id,
                "status": "ACTIVE",
                "transform": body.transform.model_dump_json(),
                "user": ctx.principal.id,
            },
        ).one()
        _audit(
            conn, ctx, "mapping.saved", "mapping",
            {"mapping_id": str(row.id), "property": body.property_iri, "version": row.version},
        )
    return {"id": str(row.id), "version": row.version, "status": "ACTIVE"}


def retire_mapping(ctx: TenantContext, mapping_id: UUID) -> dict[str, Any]:
    with tenant_tx(ctx) as conn:
        source = conn.execute(
            text("SELECT * FROM source_mappings WHERE id = :id"), {"id": mapping_id}
        ).first()
        if source is None:
            raise not_found()
        _lock_open_draft(conn, source.draft_id)
        values = {name: getattr(source, name) for name in _MAPPING_FIELDS if name != "transform"}
        row = conn.execute(
            _INSERT_MAPPING,
            values
            | {
                "id": uuid4(),
                "tenant": ctx.tenant_id,
                "draft": source.draft_id,
                "status": "RETIRED",
                "transform": Transform(**source.transform).model_dump_json(),
                "user": ctx.principal.id,
            },
        ).one()
        _audit(conn, ctx, "mapping.retired", "mapping", {"mapping_id": str(mapping_id)})
    return {"id": str(row.id), "version": row.version, "status": "RETIRED"}


def list_mappings(ctx: TenantContext, draft_id: UUID) -> list[dict[str, Any]]:
    with tenant_tx(ctx) as conn:
        return current_mappings(conn, draft_id)


_INSERT_AUTHORITY = text(
    """
    INSERT INTO authority_assertions (id, tenant_id, draft_id, property_iri, entity_type_iri,
        source_id, version, status, valid_from, valid_to, verification_method, created_by)
    VALUES (:id, :tenant, :draft, :property_iri, :entity_type_iri, :source_id,
        (SELECT COALESCE(max(version), 0) + 1 FROM authority_assertions
         WHERE draft_id = :draft AND property_iri = :property_iri
           AND entity_type_iri = :entity_type_iri AND source_id = :source_id),
        :status, :valid_from, :valid_to, :verification_method, :user)
    RETURNING id, version
    """
)


def save_authority(ctx: TenantContext, body: AuthorityBody) -> dict[str, Any]:
    with tenant_tx(ctx) as conn:
        _lock_open_draft(conn, body.draft_id)
        _require_source(conn, body.source_id)
        row = conn.execute(
            _INSERT_AUTHORITY,
            body.model_dump(exclude={"draft_id"})
            | {
                "id": uuid4(),
                "tenant": ctx.tenant_id,
                "draft": body.draft_id,
                "status": "ACTIVE",
                "user": ctx.principal.id,
            },
        ).one()
        _audit(
            conn, ctx, "authority.saved", "authority",
            {"assertion_id": str(row.id), "property": body.property_iri, "version": row.version},
        )
    return {"id": str(row.id), "version": row.version, "status": "ACTIVE"}


def retire_authority(ctx: TenantContext, assertion_id: UUID) -> dict[str, Any]:
    with tenant_tx(ctx) as conn:
        source = conn.execute(
            text("SELECT * FROM authority_assertions WHERE id = :id"), {"id": assertion_id}
        ).first()
        if source is None:
            raise not_found()
        _lock_open_draft(conn, source.draft_id)
        values = {name: getattr(source, name) for name in _AUTHORITY_FIELDS}
        row = conn.execute(
            _INSERT_AUTHORITY,
            values
            | {
                "id": uuid4(),
                "tenant": ctx.tenant_id,
                "draft": source.draft_id,
                "status": "RETIRED",
                "user": ctx.principal.id,
            },
        ).one()
        _audit(conn, ctx, "authority.retired", "authority", {"assertion_id": str(assertion_id)})
    return {"id": str(row.id), "version": row.version, "status": "RETIRED"}


def list_authority(ctx: TenantContext, draft_id: UUID) -> list[dict[str, Any]]:
    with tenant_tx(ctx) as conn:
        return current_authority(conn, draft_id)


def copy_bindings(ctx: TenantContext, conn: Connection, source: UUID, target: UUID) -> None:
    """Carry a published draft's current bindings into its successor, each as a first version."""
    new_row = {"tenant": ctx.tenant_id, "draft": target, "status": "ACTIVE",
               "user": ctx.principal.id}
    for row in candidate_mappings(current_mappings(conn, source)):
        conn.execute(
            _INSERT_MAPPING,
            row | new_row | {"id": uuid4(), "transform": json.dumps(row["transform"])},
        )
    for row in candidate_authority(current_authority(conn, source)):
        conn.execute(_INSERT_AUTHORITY, row | new_row | {"id": uuid4()})


def _comparability(mapping: dict[str, Any]) -> tuple:
    return (
        mapping["datatype"],
        mapping["value_basis"],
        mapping["unit"],
        mapping["currency_field"] is None,
        mapping["temporal_context"],
    )


def comparability_violations(mappings: list[dict[str, Any]]) -> list[str]:
    """Bindings to one property must agree on meaning, units and time (spec 5.4, AC-REC-15)."""
    by_property: dict[str, set[tuple]] = defaultdict(set)
    for mapping in mappings:
        by_property[mapping["property_iri"]].add(_comparability(mapping))
    return [
        f"{iri}: bindings differ in datatype, value basis, unit, currency or time context."
        for iri, variants in sorted(by_property.items())
        if len(variants) > 1
    ]


def unknown_term_violations(
    mappings: list[dict[str, Any]], authority: list[dict[str, Any]], known_terms: set[str]
) -> list[str]:
    referenced = {m["property_iri"] for m in mappings} | {m["class_iri"] for m in mappings}
    referenced |= {a["property_iri"] for a in authority}
    referenced |= {a["entity_type_iri"] for a in authority}
    return [f"Unknown ontology term: {term}" for term in sorted(referenced - known_terms)]
