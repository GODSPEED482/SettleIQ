"""Delegated AI investigation (spec 8). The Agent recommends and never acts."""

from fastapi import APIRouter

from app.authz.guard import TenantContext, require
from app.errors import not_implemented

router = APIRouter(prefix="/api", tags=["investigation"])


# TODO(G1 1.10 Suprava): freeze this agent route in OpenAPI as 202 with the investigation ID; the
# stub has no response model yet.
@router.post("/agent/investigate/{case_id}", status_code=202)
def start_investigation(case_id: str, ctx: TenantContext = require("case.investigate")) -> None:
    """Write the RUNNING investigation and its job in one transaction; never run it here."""
    raise not_implemented("5.7")


@router.get("/investigations/{investigation_id}")
def read_investigation(
    investigation_id: str, ctx: TenantContext = require("investigation.read")
) -> None:
    raise not_implemented("5.7")
