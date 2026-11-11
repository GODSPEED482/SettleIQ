"""Connector capabilities and the source registry (spec 4.4, 12.2)."""

from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, UploadFile

from app.authz.guard import TenantContext, require
from app.connectors.manifest import load_capability_registry
from app.errors import not_implemented
from app.sources import discovery, file_ingest, merchant_read, registry

router = APIRouter(prefix="/api", tags=["sources"])

SOURCE = ("source", "source_id")


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


# TODO(G4 4.5 Rishav): store or rotate a source's opaque secret reference (spec 11.3). Accept a
# reference, never a value, and reject one that does not resolve. Return only whether a
# reference is set. Audit create and rotate without the reference or the value. Only a grant
# holding source.credential.manage may do this: a set that inherits the other source keys but
# not this one is refused (AC-PP-10), with a test for each side. Adjust
# test_held_permission_passes_the_gate and the 501 check in test_route_guard.py.
@router.put("/sources/{source_id}/credentials")
def put_source_credentials(
    source_id: str, ctx: TenantContext = require("source.credential.manage", scope=SOURCE)
) -> None:
    raise not_implemented("4.5")


# TODO(G4 4.5 Rishav): register the PayPal webhook ID and subscribed event types for this
# source; the 4.3 trusted lookup reads them. Audit the change. Same AC-PP-10 boundary as the
# credentials route, under source.webhook.manage.
@router.put("/sources/{source_id}/webhooks")
def put_source_webhooks(
    source_id: str, ctx: TenantContext = require("source.webhook.manage", scope=SOURCE)
) -> None:
    raise not_implemented("4.5")
