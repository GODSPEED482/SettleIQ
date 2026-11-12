"""Endpoints called by the backend dispatcher. The service has no published port."""

import logging
import os
from collections.abc import Callable
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
from settleiq_agent.loop import (
    PROMPT_VERSION,
    ChatModel,
    InvestigationError,
    OpenAIChat,
    ToolSpec,
    run_investigation,
)
from settleiq_agent.tool_client import ToolAborted, ToolClient

logger = logging.getLogger("settleiq.agent")

app = FastAPI(title="SettleIQ Agent", version="0.1.0", docs_url=None, redoc_url=None,
              openapi_url=None)


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    investigation_id: UUID
    case_id: UUID
    tool_token: str
    tools: list[ToolSpec]


def get_drafter() -> Drafter:
    try:
        return OpenAIDrafter()
    except DraftingError as error:
        raise HTTPException(status_code=503, detail=str(error)) from None


def get_chat_model() -> ChatModel:
    try:
        return OpenAIChat()
    except InvestigationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from None


def get_tool_clients() -> Callable[[str], ToolClient]:
    base_url = os.environ.get("AGENT_TOOLS_URL", "http://agent-tools:8002")
    return lambda token: ToolClient(base_url, token)


@app.post("/run")
def run(
    request: RunRequest,
    model: ChatModel = Depends(get_chat_model),
    tool_clients: Callable[[str], ToolClient] = Depends(get_tool_clients),
) -> dict:
    """Run one investigation and return untrusted output for the dispatcher to validate."""
    tools = tool_clients(request.tool_token)
    try:
        outcome = run_investigation(request.case_id, tools, request.tools, model)
    except (ToolAborted, InvestigationError, OpenAIError) as error:
        logger.warning("investigation failed: %s", type(error).__name__)
        raise HTTPException(status_code=502, detail="Investigation failed.") from None
    return {
        "result": outcome.result.model_dump(),
        "model": model.name,
        "prompt_version": PROMPT_VERSION,
        "steps": outcome.steps,
        "tool_calls": outcome.tool_calls,
    }


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
