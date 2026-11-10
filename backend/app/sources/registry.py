"""Tenant-scoped source registry (spec 6.3, 12.2, AC-OPEN-01).

A source stores the connector's declared capabilities as data, so registering a new
connector needs no source-model change. Configuration holds opaque secret references only;
secret values never enter the database (spec 11.3).
"""

import json
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text

from app.audit import write_audit
from app.authz.guard import TenantContext
from app.connectors.manifest import load_connector_manifests
from app.db import global_tx, tenant_tx
from app.errors import not_found, validation_failed

_SOURCE_COLUMNS = (
    "id, connector_id, connector_version, source_type, protocol, display_name, config, "
    "capabilities, status, created_at, updated_at, "
    "(secret_reference IS NOT NULL) AS has_secret_reference"
)


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


_SECRET_KEY_PARTS = ("secret", "password", "token", "private_key", "api_key", "credential")


class CreateSourceBody(_Body):
    connector_id: str = Field(min_length=1, max_length=100)
    display_name: str = Field(min_length=1, max_length=200)
    config: dict[str, Any] = {}
    secret_reference: str | None = Field(default=None, max_length=500)

    @field_validator("config")
    @classmethod
    def _no_secret_values(cls, value: dict[str, Any]) -> dict[str, Any]:
        for key in value:
            if any(part in key.lower() for part in _SECRET_KEY_PARTS):
                raise ValueError("Configuration holds no secret values; use secret_reference.")
        return value


def _view(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "connector_id": row.connector_id,
        "connector_version": row.connector_version,
        "source_type": row.source_type,
        "protocol": row.protocol,
        "display_name": row.display_name,
        "config": row.config,
        "capabilities": row.capabilities,
        "status": row.status,
        "has_secret_reference": row.has_secret_reference,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


def list_sources(ctx: TenantContext) -> list[dict[str, Any]]:
    with tenant_tx(ctx) as conn:
        rows = conn.execute(
            text(f"SELECT {_SOURCE_COLUMNS} FROM data_sources ORDER BY created_at, id")
        ).all()
    return [_view(row) for row in rows]


def create_source(ctx: TenantContext, body: CreateSourceBody) -> dict[str, Any]:
    manifest = load_connector_manifests().get(body.connector_id)
    if manifest is None:
        raise validation_failed("Unknown connector.")
    source_id = uuid4()
    capabilities = [capability.model_dump() for capability in manifest.capabilities]
    with tenant_tx(ctx) as conn:
        conn.execute(
            text(
                """
                INSERT INTO data_sources (id, tenant_id, connector_id, connector_version,
                    source_type, protocol, display_name, config, secret_reference, capabilities,
                    created_by)
                VALUES (:id, :tenant, :connector, :version, :source_type, :protocol, :name,
                    CAST(:config AS jsonb), :secret, CAST(:capabilities AS jsonb), :user)
                """
            ),
            {
                "id": source_id,
                "tenant": ctx.tenant_id,
                "connector": manifest.connector_id,
                "version": manifest.version,
                "source_type": manifest.source_types[0],
                "protocol": manifest.protocols[0],
                "name": body.display_name,
                "config": json.dumps(body.config),
                "secret": body.secret_reference,
                "capabilities": json.dumps(capabilities),
                "user": ctx.principal.id,
            },
        )
        write_audit(
            conn, ctx.tenant_id, ctx.principal, "source.created", "source",
            {"source_id": str(source_id), "connector_id": manifest.connector_id},
        )
        row = conn.execute(
            text(f"SELECT {_SOURCE_COLUMNS} FROM data_sources WHERE id = :id"),
            {"id": source_id},
        ).one()
    return _view(row)


def get_source(ctx: TenantContext, source_id: UUID) -> dict[str, Any]:
    with tenant_tx(ctx) as conn:
        row = conn.execute(
            text(f"SELECT {_SOURCE_COLUMNS} FROM data_sources WHERE id = :id"),
            {"id": source_id},
        ).first()
    if row is None:
        raise not_found()
    return _view(row)


def webhook_source(source_id: UUID) -> dict[str, Any] | None:
    """Trusted registration lookup for webhook ingress (spec 10.4, POL-15).

    Uses the narrow SECURITY DEFINER function, so it returns one source's tenant and
    configuration without exposing the source registry across tenants.
    """
    with global_tx() as conn:
        row = conn.execute(
            text(
                "SELECT tenant_id, connector_id, config FROM resolve_webhook_source(:id)"
            ),
            {"id": source_id},
        ).first()
    if row is None:
        return None
    return {"tenant_id": row.tenant_id, "connector_id": row.connector_id, "config": row.config}
