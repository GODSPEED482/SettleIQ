"""Private Worker API, served as a separate process with no published port (AC-AUTHZ-17)."""

from fastapi import APIRouter, FastAPI

from app.authz.guard import TenantContext, require
from app.bootstrap import finish_app
from app.errors import not_implemented

router = APIRouter(tags=["internal"])


@router.post("/internal/executions/{execution_id}/run")
def run_execution(execution_id: str, ctx: TenantContext = require("playbook.execute")) -> None:
    raise not_implemented("5.6")


def create_worker_app() -> FastAPI:
    app = FastAPI(title="SettleIQ Worker", version="0.1.0", docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.include_router(router)
    return finish_app(app)


app = create_worker_app()
