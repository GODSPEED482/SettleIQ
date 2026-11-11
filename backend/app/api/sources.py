"""Connector capabilities and the source registry (spec 4.4, 12.2)."""

from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, UploadFile

from app.authz.guard import TenantContext, require
from app.connectors.manifest import load_capability_registry, load_connector_manifests
from app.sources import discovery, file_ingest, merchant_read, registry
from app.webhooks import processor

router = APIRouter(prefix="/api", tags=["sources"])

SOURCE = ("source", "source_id")


# §12.2 omits a connector list; the Sources screen (4.7) offers these when a source is
# registered. Manifests are declarations only and hold no secret values.
@router.get("/connectors")
def list_connectors(ctx: TenantContext = require("connector.read")) -> list[dict]:
    manifests = load_connector_manifests()
    return [manifests[key].model_dump() for key in sorted(manifests)]


@router.get("/connectors/capabilities")
def connector_capabilities(ctx: TenantContext = require("connector.read")) -> list[dict]:
    return [capability.model_dump() for capability in load_capability_registry().values()]


@router.get("/sources")
def list_sources(ctx: TenantContext = require("source.read")) -> list[dict]:
    return registry.list_sources(ctx)


@router.post("/sources", status_code=201)
def create_source(
    body: registry.CreateSourceBody, ctx: TenantContext = require("source.create")
) -> dict:
    return registry.create_source(ctx, body)


@router.post("/sources/{source_id}/discover")
def discover_source(
    source_id: str, ctx: TenantContext = require("source.discover", scope=SOURCE)
) -> dict:
    return discovery.discover(ctx, source_id)


@router.post("/sources/{source_id}/sync")
def sync_source(
    source_id: str, ctx: TenantContext = require("source.sync", scope=SOURCE)
) -> dict:
    return merchant_read.sync(ctx, source_id)


@router.post("/sources/{source_id}/files")
async def upload_source_file(
    source_id: str,
    file: Annotated[UploadFile, File()],
    kind: Annotated[Literal["BANK_CSV", "BRR"], Form()],
    ctx: TenantContext = require("source.file.upload", scope=SOURCE),
) -> dict:
    return file_ingest.ingest(ctx, source_id, await file.read(), file.filename or "file", kind)


# AC-PP-10: holding the other source keys, by inheritance or otherwise, never reaches these.
@router.put("/sources/{source_id}/credentials")
def put_source_credentials(
    source_id: str,
    body: registry.CredentialBody,
    ctx: TenantContext = require("source.credential.manage", scope=SOURCE),
) -> dict:
    return registry.put_credentials(ctx, source_id, body)


# Spec v1.7 §11.3, AC-PP-14: the write-only intake. The value is accepted once and never returned.
@router.put("/sources/{source_id}/credential-value")
def put_source_credential_value(
    source_id: str,
    body: registry.CredentialValueBody,
    ctx: TenantContext = require("source.credential.manage", scope=SOURCE),
) -> dict:
    return registry.put_credential_value(ctx, source_id, body)


@router.put("/sources/{source_id}/webhooks")
def put_source_webhooks(
    source_id: str,
    body: registry.WebhookBody,
    ctx: TenantContext = require("source.webhook.manage", scope=SOURCE),
) -> dict:
    return registry.put_webhooks(ctx, source_id, body)


# §12.2 omits these; §10.4 requires failed processing, replay and dead-letter state to be
# exposed. The list carries status only, never payloads.
@router.get("/sources/{source_id}/webhook-events")
def list_webhook_events(
    source_id: str, ctx: TenantContext = require("source.read", scope=SOURCE)
) -> list[dict]:
    return processor.list_inbox_rows(ctx, source_id)


@router.post("/sources/{source_id}/webhook-events/{event_id}/replay")
def replay_webhook_event(
    source_id: str,
    event_id: str,
    ctx: TenantContext = require("source.webhook.manage", scope=SOURCE),
) -> dict:
    return processor.replay_webhook(ctx, source_id, event_id)
