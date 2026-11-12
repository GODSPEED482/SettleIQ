"""Authorization for Agent tool calls (POL-08, spec 8.2).

The Agent authenticates with the delegated token the dispatcher minted for one investigation.
The token names the investigation, tenant, case and invoker, and nothing else. On every call the
guard checks the investigation is still RUNNING for that case and invoker, then resolves the
Agent's set intersected with the invoker's current access, so revoking the invoker's key stops
the next call. A refused call is recorded in the trace in its own transaction.
"""

import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from fastapi import Depends, Request
from sqlalchemy import text

from app.authz.access import (
    AccessGateway,
    Decision,
    EffectiveAccess,
    Principal,
    PrincipalKind,
    get_gateway,
)
from app.authz.catalogue import get_catalogue
from app.authz.guard import Access, _declare
from app.db import scoped_tx
from app.domain_packs.manifest import AgentTool
from app.errors import missing_permission, unauthenticated
from app.investigation.tokens import TokenError, verify_token
from app.investigation.tool_impl import digest

logger = logging.getLogger("settleiq.agent_tools")

_TRACE_SQL = text(
    """
    INSERT INTO investigation_tool_calls (id, tenant_id, investigation_id, tool_name,
        tool_version, permission, agent_principal_id, invoker_id, input_hash, input,
        output_hash, output, evidence_ids, outcome, error)
    VALUES (:id, :tenant, :investigation, :tool, :version, :permission, :agent, :invoker,
        :input_hash, CAST(:input AS jsonb), :output_hash, CAST(:output AS jsonb),
        CAST(:evidence AS uuid[]), :outcome, :error)
    """
)


@dataclass(frozen=True)
class ToolContext:
    tenant_id: UUID
    investigation_id: UUID
    case_id: UUID
    pack_namespace: str
    invoker_id: UUID
    agent: Principal
    access: EffectiveAccess
    tool_version: str


def record_call(conn: Any, ctx: ToolContext, tool: AgentTool, *, input_hash: str,
                input: str, outcome: str, output_hash: str | None = None,
                output: str | None = None, evidence: list[str] | None = None,
                error: str | None = None) -> None:
    conn.execute(
        _TRACE_SQL,
        {
            "id": uuid4(), "tenant": ctx.tenant_id, "investigation": ctx.investigation_id,
            "tool": tool.name, "version": ctx.tool_version, "permission": tool.permission,
            "agent": ctx.agent.id, "invoker": ctx.invoker_id, "input_hash": input_hash,
            "input": input, "output_hash": output_hash, "output": output,
            "evidence": evidence or [], "outcome": outcome, "error": error,
        },
    )


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise unauthenticated()
    return token.strip()


def require_agent_tool(tool: AgentTool, tool_version: str) -> Any:
    """The tool's one permission, for the Agent acting for one invoker on one case."""

    def guard(request: Request, gateway: AccessGateway = Depends(get_gateway)) -> ToolContext:
        try:
            token = verify_token(_bearer(request))
        except TokenError as error:
            logger.info("agent token refused: %s", error)
            raise unauthenticated() from None
        with scoped_tx(token.tenant_id) as conn:
            run = conn.execute(
                text(
                    """
                    SELECT case_id, invoked_by, agent_principal_id, pack_namespace, status
                    FROM investigations WHERE id = :id
                    """
                ),
                {"id": token.investigation_id},
            ).one_or_none()
        if (run is None or run.status != "RUNNING" or run.case_id != token.case_id
                or run.invoked_by != token.invoker_id):
            raise unauthenticated()
        agent = Principal(run.agent_principal_id, PrincipalKind.AGENT)
        access = gateway.resolve_agent(agent, token.tenant_id, token.invoker_id)
        ctx = ToolContext(token.tenant_id, token.investigation_id, token.case_id,
                          run.pack_namespace, token.invoker_id, agent,
                          access or EffectiveAccess([], get_catalogue()), tool_version)
        allowed = access is not None and access.permits(tool.permission)
        spec = get_catalogue().get(tool.permission)
        if spec.sensitivity != "normal":
            gateway.record_decision(
                Decision(token.tenant_id, agent, tool.permission, spec.resource, None, allowed)
            )
        if not allowed:
            with scoped_tx(token.tenant_id) as conn:
                record_call(conn, ctx, tool, input_hash=digest({}), input="{}",
                            outcome="DENIED", error="MISSING_PERMISSION")
            raise missing_permission(tool.permission)
        return ctx

    return _declare(guard, Access("PERMISSION", tool.permission))
