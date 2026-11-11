"""Endpoints called by the backend dispatcher. The service has no published port."""

import logging
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException
from openai import OpenAIError
from pydantic import BaseModel, ConfigDict

from settleiq_agent.drafting import (
    Drafter,
    DraftingError,
    DraftRequest,
    OntologyProposal,
    OpenAIDrafter,
)

logger = logging.getLogger("settleiq.agent")

app = FastAPI(title="SettleIQ Agent", version="0.1.0", docs_url=None, redoc_url=None,
              openapi_url=None)


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    investigation_id: UUID
    case_id: UUID
    tool_token: str


def get_drafter() -> Drafter:
    try:
        return OpenAIDrafter()
    except DraftingError as error:
        raise HTTPException(status_code=503, detail=str(error)) from None


# TODO(G5 5.7 Swapnaneel): run run_investigation with a ToolClient bound to the request's
# token and return its output; the dispatcher validates it.
@app.post("/run")
def run(request: RunRequest) -> dict:
    """Run one investigation and return untrusted output for the dispatcher to validate."""
    raise HTTPException(status_code=501, detail="Not implemented yet (task 5.7).")


@app.post("/draft-ontology")
def draft_ontology(
    request: DraftRequest, drafter: Drafter = Depends(get_drafter)
) -> OntologyProposal:
    """Draft a proposal from discovered metadata. The dispatcher revalidates the result."""
    try:
        return drafter.draft(request)
    except (DraftingError, OpenAIError) as error:
        logger.warning("draft generation failed: %s", type(error).__name__)
        raise HTTPException(status_code=502, detail="Draft generation failed.") from None
