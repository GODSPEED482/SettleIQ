"""Private Agent tool API, served as a separate process with no published port (spec 8.2).

Routes are generated from the server-side registry, so a tool outside it has no route. The
agent can reach this app, and never app/worker_api.py.

Each call is authorized by app/investigation/tool_guard.py, runs one implementation from
app/investigation/tool_impl.py inside the investigated case's scope, and appends an
investigation_tool_calls row in the same transaction as the read, so a result the Agent saw is
always in the trace. Arguments are typed values: no tool runs SQL text or reaches a provider
(AC-AI-03).
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Body, FastAPI
from pydantic import ValidationError

from app.authz.catalogue import get_catalogue
from app.bootstrap import finish_app
from app.db import scoped_tx
from app.domain_packs.manifest import AgentTool, load_domain_packs
from app.errors import ApiError, validation_failed
from app.investigation.tool_guard import ToolContext, record_call, require_agent_tool
from app.investigation.tool_impl import (
    Run,
    canonical,
    digest,
    implementation,
    redact,
    registry_version,
)
from app.investigation.tools import build_tool_registry

TOOL_PREFIX = "/internal/agent-tools"


def _uuids(values: frozenset[str]) -> list[str]:
    out = []
    for value in sorted(values):
        try:
            out.append(str(UUID(value)))
        except ValueError:
            continue
    return out


def _add_tool_route(router: APIRouter, tool: AgentTool, version: str) -> None:
    model, function = implementation(tool)

    def run_tool(
        body: dict[str, Any] = Body(default={}),
        ctx: ToolContext = require_agent_tool(tool, version),
    ) -> dict[str, Any]:
        trace = {"input_hash": digest(body), "input": canonical(body)}
        try:
            args = model.model_validate(body)
        except ValidationError:
            with scoped_tx(ctx.tenant_id) as conn:
                record_call(conn, ctx, tool, **trace, outcome="ERROR",
                            error="INVALID_ARGUMENTS")
            raise validation_failed("The tool arguments do not match its schema.") from None
        try:
            with scoped_tx(ctx.tenant_id) as conn:
                run = Run(conn, ctx.tenant_id, ctx.investigation_id, ctx.case_id,
                          ctx.pack_namespace, ctx.access)
                output = function(run, args)
                evidence = _uuids(output.evidence_ids)
                record_call(conn, ctx, tool, **trace, outcome="OK",
                            output_hash=digest(output.result),
                            output=canonical(redact(output.result)), evidence=evidence)
        except ApiError as error:
            with scoped_tx(ctx.tenant_id) as conn:
                record_call(conn, ctx, tool, **trace, outcome="ERROR", error=error.code)
            raise
        return {"result": output.result, "evidence_ids": evidence}

    router.add_api_route(f"{TOOL_PREFIX}/{tool.name}", run_tool, methods=["POST"], name=tool.name)


def create_agent_tools_app() -> FastAPI:
    app = FastAPI(title="SettleIQ Agent Tools", version="0.1.0", docs_url=None, redoc_url=None,
                  openapi_url=None)
    router = APIRouter(tags=["agent-tools"])
    tools = build_tool_registry(load_domain_packs(), get_catalogue())
    version = registry_version(tools)
    for tool in tools:
        _add_tool_route(router, tool, version)
    app.include_router(router)
    return finish_app(app)


app = create_agent_tools_app()
