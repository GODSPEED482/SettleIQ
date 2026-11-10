"""Connector capabilities and the source registry (spec 4.4, 12.2)."""

from fastapi import APIRouter

from app.authz.guard import TenantContext, require
from app.connectors.manifest import load_capability_registry
from app.errors import not_implemented
from app.sources import registry

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


# TODO(G2 2.5 Rahul): for PostgreSQL sources, write tables, columns, types, keys, foreign keys,
# uniqueness, nullability and indexes to source_objects.schema_snapshot (AC-ONT-01), and profile
# aggregates to source_objects.profile: nulls, cardinality, duplicate identifiers, numeric
# ranges, currency, temporal and status distributions (AC-ONT-02). Aggregates only; no raw rows
# or secrets. Audit the run, and adjust test_held_permission_passes_the_gate (200 or 501 only).
# TODO(G2 2.6 Rahul): for CSV sources, discover the header, inferred types and the same profile
# into a source_objects row of object_type CSV, so generation sees one shape for both. Read the
# stored raw-evidence copy; the upload route itself is task 3.6.
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
