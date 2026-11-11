"""Delegated AI investigation (spec 8). The Agent recommends and never acts."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel

from app.authz.guard import TenantContext, require
from app.investigation import service

router = APIRouter(prefix="/api", tags=["investigation"])


class InvestigationStarted(BaseModel):
    """The 202 contract, frozen under task 1.10: the run continues in the dispatcher."""

    investigation_id: UUID
    status: Literal["RUNNING"]


@router.post(
    "/agent/investigate/{case_id}", status_code=202, response_model=InvestigationStarted
)
def start_investigation(
    case_id: UUID, ctx: TenantContext = require("case.investigate")
) -> dict:
    """Write the RUNNING investigation and its job in one transaction; never run it here."""
    return service.start(ctx, case_id)


@router.get("/investigations/{investigation_id}")
def read_investigation(
    investigation_id: UUID, ctx: TenantContext = require("investigation.read")
) -> dict:
    """The investigation, its validated result, PROPOSED classification and tool trace. Evidence
    IDs come from the trace, so they resolve without trusting the model's text (AC-AI-04)."""
    return service.read(ctx, investigation_id)
