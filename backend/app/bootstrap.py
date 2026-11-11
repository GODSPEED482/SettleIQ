"""Startup validation shared by the public API and the private Worker API."""

from fastapi import FastAPI

from app.authz.catalogue import get_catalogue
from app.authz.guard import validate_routes
from app.connectors.manifest import load_connector_manifests
from app.domain_packs.manifest import load_domain_packs
from app.domain_packs.playbooks import load_playbooks
from app.domain_packs.registry import resolve_pack_entrypoints
from app.errors import install_error_handlers
from app.investigation.tools import build_tool_registry
from app.release.capability_manifest import check_packaged
from app.semantics.artifacts import core_artifact, pack_artifacts


def validate_platform(app: FastAPI) -> None:
    """Fail startup on any undeclared route, invalid manifest or playbook definition,
    prohibited agent tool, or a packaged capability manifest that differs from the registered
    implementations (AC-REL-01)."""
    catalogue = get_catalogue()
    validate_routes(app, catalogue)
    load_connector_manifests()
    packs = load_domain_packs()
    resolve_pack_entrypoints(packs)
    load_playbooks(packs)
    core_artifact()
    for pack in packs.values():
        pack_artifacts(pack)
    build_tool_registry(packs, catalogue)
    check_packaged()


def finish_app(app: FastAPI) -> FastAPI:
    install_error_handlers(app)
    validate_platform(app)
    return app
