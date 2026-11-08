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
