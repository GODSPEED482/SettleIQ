"""Public API. The private Worker route lives in app/worker_api.py and is not mounted here."""

from fastapi import FastAPI

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


# TODO(G1 1.10 Suprava): freeze OpenAPI; stubs have no request or response models to freeze yet.
# TODO(G1 OPEN-14 Suprava): spec 12.1 requires a versioned API; routes are unversioned /api.
def create_app() -> FastAPI:
    app = FastAPI(title="SettleIQ", version="0.1.0")
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
