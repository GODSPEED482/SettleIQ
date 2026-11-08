"""Read-only views of the permission catalogue and system-managed sets."""

from dataclasses import asdict

from fastapi import APIRouter

from app.authz.catalogue import get_catalogue, load_permission_sets
from app.authz.guard import TenantContext, require

router = APIRouter(prefix="/api", tags=["authorization"])


# TODO(G1 1.3 Rishav): owner review of these read-only catalogue views.
@router.get("/permissions")
def list_permissions(ctx: TenantContext = require("role.read")) -> list[dict]:
    return [asdict(spec) for spec in get_catalogue()]


@router.get("/permission-sets")
def list_permission_sets(ctx: TenantContext = require("role.read")) -> list[dict]:
    return [asdict(spec) for spec in load_permission_sets(get_catalogue())]
