"""Connector capabilities and the source registry (spec 4.4, 12.2)."""

from fastapi import APIRouter

from app.authz.guard import TenantContext, require
from app.connectors.manifest import load_capability_registry
from app.errors import not_implemented

router = APIRouter(prefix="/api", tags=["sources"])

SOURCE = ("source", "source_id")


@router.get("/connectors/capabilities")
def connector_capabilities(ctx: TenantContext = require("connector.read")) -> list[dict]:
    return [capability.model_dump() for capability in load_capability_registry().values()]


@router.get("/sources")
def list_sources(ctx: TenantContext = require("source.read")) -> None:
    raise not_implemented("1.7")


@router.post("/sources")
def create_source(ctx: TenantContext = require("source.create")) -> None:
    raise not_implemented("1.7")


@router.post("/sources/{source_id}/discover")
def discover_source(
    source_id: str, ctx: TenantContext = require("source.discover", scope=SOURCE)
) -> None:
    raise not_implemented("2.5")


@router.post("/sources/{source_id}/sync")
def sync_source(source_id: str, ctx: TenantContext = require("source.sync", scope=SOURCE)) -> None:
    raise not_implemented("3.5")


@router.post("/sources/{source_id}/files")
def upload_source_file(
    source_id: str, ctx: TenantContext = require("source.file.upload", scope=SOURCE)
) -> None:
    raise not_implemented("3.6")


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
