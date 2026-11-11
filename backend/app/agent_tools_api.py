"""Private Agent tool API, served as a separate process with no published port (spec 8.2).

Routes are generated from the server-side registry, so a tool outside it has no route. The
agent can reach this app, and never app/worker_api.py.
"""

from fastapi import APIRouter, FastAPI

from app.authz.catalogue import get_catalogue
from app.authz.guard import TenantContext, require
from app.bootstrap import finish_app
from app.domain_packs.manifest import AgentTool, load_domain_packs
from app.errors import not_implemented
from app.investigation.tools import build_tool_registry

TOOL_PREFIX = "/internal/agent-tools"


# TODO(G5 5.7 Swapnaneel): implement all 11 registry tools behind these generated routes, with
# the boundaries in spec 8.2:
# - get_ontology_context: the active, pinned tenant composition only;
# - get_source_authority: the exact fact and entity scope;
# - get_source_records: tenant and invoking-member scope;
# - trace_domain_relationships: registered domain relationships only;
# - get_case_evidence: raw payload excerpts also need evidence.raw.read;
# - get_property_observations: keep provenance and value and time metadata;
# - get_entity_resolution_trace: the candidate and evidence trace;
# - classify_domain_case: a taxonomy the active pack registers, stored as PROPOSED only;
# - recommend_playbook: eligible templates only;
# - validate_playbook: a dry run with no side effects;
# - calculate_settlement_variance (finops pack): packs/finops/settlement.py, not LLM arithmetic.
# Each call verifies the delegated token, checks tenant, case and evidence scope server-side,
# returns only what that scope allows (AC-AI-01, AC-AI-05), and appends an
# investigation_tool_calls row with redacted inputs and outputs, the permission checked and the
# evidence IDs returned. No tool runs SQL text or reaches a provider (AC-AI-03).
def _add_tool_route(router: APIRouter, tool: AgentTool) -> None:
    def run_tool(ctx: TenantContext = require(tool.permission)) -> None:
        raise not_implemented("5.7")

    router.add_api_route(f"{TOOL_PREFIX}/{tool.name}", run_tool, methods=["POST"], name=tool.name)


def create_agent_tools_app() -> FastAPI:
    app = FastAPI(title="SettleIQ Agent Tools", version="0.1.0", docs_url=None, redoc_url=None,
                  openapi_url=None)
    router = APIRouter(tags=["agent-tools"])
    for tool in build_tool_registry(load_domain_packs(), get_catalogue()):
        _add_tool_route(router, tool)
    app.include_router(router)
    return finish_app(app)


app = create_agent_tools_app()
