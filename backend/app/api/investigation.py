"""Delegated AI investigation (spec 8). The Agent recommends and never acts."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel

from app.authz.guard import TenantContext, require
from app.errors import not_implemented

router = APIRouter(prefix="/api", tags=["investigation"])


class InvestigationStarted(BaseModel):
    """The 202 contract, frozen under task 1.10: the run continues in the dispatcher."""

    investigation_id: UUID
    status: Literal["RUNNING"]


@router.post(
    "/agent/investigate/{case_id}", status_code=202, response_model=InvestigationStarted
)
def start_investigation(
    case_id: str, ctx: TenantContext = require("case.investigate")
) -> InvestigationStarted:
    """Write the RUNNING investigation and its job in one transaction; never run it here."""
    raise not_implemented("5.7")


@router.get("/investigations/{investigation_id}")
def read_investigation(
    investigation_id: str, ctx: TenantContext = require("investigation.read")
) -> None:
    raise not_implemented("5.7")
