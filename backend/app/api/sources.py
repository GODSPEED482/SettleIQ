"""Connector capabilities and the source registry (spec 4.4, 12.2)."""

from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, UploadFile

from app.authz.guard import TenantContext, require
from app.connectors.manifest import load_capability_registry, load_connector_manifests
from app.sources import discovery, file_ingest, merchant_read, registry

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


# TODO(G4 4.2 Rahul): PayPal sync authenticates with OAuth client credentials (AC-PP-01), then
# backfills through Transaction Search (transactions.read) in windows of at most 31 days,
# paging until done, and fetches details (captures.read) for each capture found. Store each
# response as RECORD evidence with provenance `provider_api` and persist records as
# conventions.py describes for API reads. Reuse the token until it expires, follow the
# manifest's retry policy, and treat Transaction Search as lagging, never as an event stream
# (spec 10.3). merchant_read.py answers 501 for PayPal until then.
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


@router.put("/sources/{source_id}/webhooks")
def put_source_webhooks(
    source_id: str,
    body: registry.WebhookBody,
    ctx: TenantContext = require("source.webhook.manage", scope=SOURCE),
) -> dict:
    return registry.put_webhooks(ctx, source_id, body)
