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


# TODO(G5 5.7 Swapnaneel): migration 0017 adds investigations and investigation_tool_calls,
# append-only, with a write-once terminal trigger on the investigation (see CLAUDE.md: never
# an empty placeholder migration). Write the RUNNING row and an INVESTIGATION agent_jobs row in
# one transaction and return 202. Each stored result and trace carries tenant, case, invoker,
# model, prompt and tool versions, evidence references and outcome (AC-INV-01).
@router.post(
    "/agent/investigate/{case_id}", status_code=202, response_model=InvestigationStarted
)
def start_investigation(
    case_id: str, ctx: TenantContext = require("case.investigate")
) -> InvestigationStarted:
    """Write the RUNNING investigation and its job in one transaction; never run it here."""
    raise not_implemented("5.7")


# TODO(G5 5.7 Swapnaneel): read the investigation with its validated result, PROPOSED
# classification and tool trace. Evidence IDs resolve independently of the model's text
# (AC-AI-04).
@router.get("/investigations/{investigation_id}")
def read_investigation(
    investigation_id: str, ctx: TenantContext = require("investigation.read")
) -> None:
    raise not_implemented("5.7")
