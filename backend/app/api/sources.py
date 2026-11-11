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


@router.put("/sources/{source_id}/credentials")
def put_source_credentials(
    source_id: str, ctx: TenantContext = require("source.credential.manage", scope=SOURCE)
) -> None:
    raise not_implemented("4.5")


@router.put("/sources/{source_id}/webhooks")
def put_source_webhooks(
    source_id: str, ctx: TenantContext = require("source.webhook.manage", scope=SOURCE)
) -> None:
    raise not_implemented("4.5")
