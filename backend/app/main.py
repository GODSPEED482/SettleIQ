"""Public API. The private Worker route lives in app/worker_api.py and is not mounted here."""

from fastapi import FastAPI, Request, Response

from app.api import (
    authz,
    domain,
    health,
    identity,
    investigation,
    platform,
    playbooks,
    semantics,
    sources,
    tenants,
    webhooks,
)
from app.bootstrap import finish_app

# Spec 12.1 versioning, ratified under OPEN-14: /api is version 1. Version 1 changes are additive
# only; a breaking change ships under /api/v2 alongside it.
API_VERSION = "1"
API_VERSION_HEADER = "API-Version"


# TODO(G1 1.10 Suprava): freeze OpenAPI; stubs have no request or response models to freeze yet.
def create_app() -> FastAPI:
    app = FastAPI(title="SettleIQ", version=API_VERSION)

    @app.middleware("http")
    async def stamp_api_version(request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers[API_VERSION_HEADER] = API_VERSION
        return response

    for module in (
        health,
        identity,
        authz,
        tenants,
        sources,
        webhooks,
        semantics,
        domain,
        investigation,
        playbooks,
        platform,
    ):
        app.include_router(module.router)
    return finish_app(app)


app = create_app()
