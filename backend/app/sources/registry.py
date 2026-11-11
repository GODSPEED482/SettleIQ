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
from app.connectors.manifest import load_connector_manifests, select_binding
from app.db import global_tx, tenant_tx
from app.errors import conflict, missing_permission, not_found, validation_failed
from app.sources.secrets import is_resolvable

CREDENTIAL_KEY = "source.credential.manage"

_SOURCE_COLUMNS = (
    "id, connector_id, connector_version, source_type, protocol, display_name, config, "
    "capabilities, status, created_at, updated_at, "
    "(secret_reference IS NOT NULL) AS has_secret_reference"
)


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


_SECRET_KEY_PARTS = ("secret", "password", "token", "private_key", "api_key", "credential")
_WEBHOOK_KEYS = ("webhook", "webhook_id")


class CreateSourceBody(_Body):
    connector_id: str = Field(min_length=1, max_length=100)
    # Optional; must be declared by the manifest, and default to its first entry.
    protocol: str | None = Field(default=None, min_length=1, max_length=100)
    source_type: str | None = Field(default=None, min_length=1, max_length=100)
    display_name: str = Field(min_length=1, max_length=200)
    config: dict[str, Any] = {}
    secret_reference: str | None = Field(default=None, max_length=500)

    @field_validator("config")
    @classmethod
    def _no_secret_values(cls, value: dict[str, Any]) -> dict[str, Any]:
        for key in value:
            if any(part in key.lower() for part in _SECRET_KEY_PARTS):
                raise ValueError("Configuration holds no secret values; use secret_reference.")
            # Webhook ingress trusts this configuration, so only source.webhook.manage sets it.
            if key.lower() in _WEBHOOK_KEYS:
                raise ValueError("Register webhooks through the source's webhooks route.")
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
    # A reference given at registration is a credential operation like PUT credentials, so
    # source.create alone does not cover it (AC-PP-10).
    if body.secret_reference is not None and not ctx.access.permits(CREDENTIAL_KEY):
        raise missing_permission(CREDENTIAL_KEY)
    try:
        protocol, source_type = select_binding(manifest, body.protocol, body.source_type)
    except ValueError as error:
        raise validation_failed(str(error)) from None
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
                "source_type": source_type,
                "protocol": protocol,
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


def _source_uuid(source_id: str) -> UUID:
    try:
        return UUID(source_id)
    except ValueError:
        raise not_found() from None


class CredentialBody(_Body):
    """A reference only. There is deliberately no field that could carry a value (spec 11.3)."""

    secret_reference: str = Field(min_length=1, max_length=500)


def put_credentials(ctx: TenantContext, source_id: str, body: CredentialBody) -> dict[str, Any]:
    """Store or rotate a source's secret reference (task 4.5, AC-PP-10).

    The audit row names the source only: neither the reference nor the value is recorded.
    """
    source_uuid = _source_uuid(source_id)
    if not is_resolvable(body.secret_reference):
        raise conflict("SECRET_UNRESOLVED", "The source's secret reference cannot be resolved.")
    with tenant_tx(ctx) as conn:
        previous = conn.execute(
            text("SELECT secret_reference FROM data_sources WHERE id = :id FOR UPDATE"),
            {"id": source_uuid},
        ).first()
        if previous is None:
            raise not_found()
        conn.execute(
            text(
                "UPDATE data_sources SET secret_reference = :secret, updated_at = now() "
                "WHERE id = :id"
            ),
            {"id": source_uuid, "secret": body.secret_reference},
        )
        action = "created" if previous.secret_reference is None else "rotated"
        write_audit(
            conn, ctx.tenant_id, ctx.principal, f"source.credential.{action}", "source",
            {"source_id": str(source_uuid)},
        )
        row = conn.execute(
            text(f"SELECT {_SOURCE_COLUMNS} FROM data_sources WHERE id = :id"),
            {"id": source_uuid},
        ).one()
    return _view(row)


class WebhookBody(_Body):
    webhook_id: str = Field(min_length=1, max_length=100)
    event_types: list[str] = Field(min_length=1)


def _allowed_event_types(capabilities: list[dict[str, Any]]) -> set[str] | None:
    """The event types the source's pinned `webhooks.receive` contract accepts, if any."""
    for capability in capabilities:
        if capability["key"] == "webhooks.receive":
            schema = capability["parameters"].get("properties", {}).get("event_types", {})
            return set(schema.get("items", {}).get("enum", [])) or None
    return None


def put_webhooks(ctx: TenantContext, source_id: str, body: WebhookBody) -> dict[str, Any]:
    """Register the provider webhook ID and event types the 4.3 trusted lookup reads.

    They live in `config.webhook`, which `resolve_webhook_source` already returns.
    """
    source_uuid = _source_uuid(source_id)
    if len(set(body.event_types)) != len(body.event_types):
        raise validation_failed("Event types must be unique.")
    with tenant_tx(ctx) as conn:
        source = conn.execute(
            text("SELECT capabilities FROM data_sources WHERE id = :id FOR UPDATE"),
            {"id": source_uuid},
        ).first()
        if source is None:
            raise not_found()
        allowed = _allowed_event_types(source.capabilities)
        if allowed is None:
            raise conflict("WEBHOOKS_UNSUPPORTED", "This source cannot register webhooks.")
        unknown = sorted(set(body.event_types) - allowed)
        if unknown:
            raise validation_failed(f"Unsupported event types: {', '.join(unknown)}.")
        webhook = {"webhook_id": body.webhook_id, "event_types": body.event_types}
        conn.execute(
            text(
                """
                UPDATE data_sources
                SET config = jsonb_set(config, '{webhook}', CAST(:webhook AS jsonb)),
                    updated_at = now()
                WHERE id = :id
                """
            ),
            {"id": source_uuid, "webhook": json.dumps(webhook)},
        )
        write_audit(
            conn, ctx.tenant_id, ctx.principal, "source.webhook.configured", "source",
            {"source_id": str(source_uuid), **webhook},
        )
        row = conn.execute(
            text(f"SELECT {_SOURCE_COLUMNS} FROM data_sources WHERE id = :id"),
            {"id": source_uuid},
        ).one()
    return _view(row)
