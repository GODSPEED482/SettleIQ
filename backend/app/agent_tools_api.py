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
