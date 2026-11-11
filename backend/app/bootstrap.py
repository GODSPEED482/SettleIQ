"""Startup validation shared by the public API and the private Worker API."""

from fastapi import FastAPI

from app.authz.catalogue import get_catalogue
from app.authz.guard import validate_routes
from app.connectors.manifest import load_connector_manifests
from app.domain_packs.manifest import load_domain_packs
from app.domain_packs.registry import resolve_pack_entrypoints
from app.errors import install_error_handlers
from app.investigation.tools import build_tool_registry
from app.semantics.artifacts import core_artifact, pack_artifacts


def validate_platform(app: FastAPI) -> None:
    """Fail startup on any undeclared route, invalid manifest or prohibited agent tool."""
    catalogue = get_catalogue()
    validate_routes(app, catalogue)
    load_connector_manifests()
    packs = load_domain_packs()
    resolve_pack_entrypoints(packs)
    core_artifact()
    for pack in packs.values():
        pack_artifacts(pack)
    build_tool_registry(packs, catalogue)


# TODO(G5 5.4 Rishav): generate the capability manifest from registered implementations
# (id, version, domain, connector, contract version, kind, dependencies, entrypoint and SHA-256
# of its packaged source), package it in the image, and fail startup when it and the
# registered implementations differ (spec 9.7.1, AC-REL-01).
def finish_app(app: FastAPI) -> FastAPI:
    install_error_handlers(app)
    validate_platform(app)
    return app
