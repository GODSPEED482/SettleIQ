"""Connector capabilities and the source registry (spec 4.4, 12.2)."""

from fastapi import APIRouter

from app.authz.guard import TenantContext, require
from app.connectors.manifest import load_capability_registry
from app.errors import not_implemented
from app.sources import discovery, registry

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


# TODO(G3 3.5 Rahul): for merchant PostgreSQL sources, read rows of the discovered tables over
# the read-only secret reference. Store each response as RECORD evidence with provenance
# `database`, then persist one envelope per row with records.persist_record. external_id is the
# table's primary key. Keep values as read: no float money, no dropped columns. Projection
# (3.1) does the rest. Other connectors still answer 501 until 4.2. Audit the sync, and adjust
# test_held_permission_passes_the_gate and the 501 check in test_route_guard.py.
@router.post("/sources/{source_id}/sync")
def sync_source(source_id: str, ctx: TenantContext = require("source.sync", scope=SOURCE)) -> None:
    raise not_implemented("3.5")


# TODO(G3 3.6 Rahul): accept a bank CSV or BRR CSV. Store the original bytes first, as FILE or
# REPORT evidence with provenance `provider_report`, `synthetic_reference` or `tenant_file`,
# then write one record per row as conventions.py describes. Parse BRR columns by name, keep
# unknown columns, preserve UTF-8 and the file hash, and reject malformed required fields or
# inconsistent totals (spec 10.3.1). A provider report and the 3.8 fixture take this same path;
# only the provenance differs, and it must reach every later stage.
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
