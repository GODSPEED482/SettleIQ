"""Domain packs and the ontology lifecycle (spec 5.7, POL-12)."""

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from app.authz.guard import TenantContext, require
from app.errors import not_implemented
from app.semantics import store

router = APIRouter(prefix="/api", tags=["semantics"])

# TODO(G1 1.10 Suprava): add mapping and authority reads (spec 12.2 omits them), guarded by the
# existing mapping.read and authority.read keys.


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ManagePackBody(_Body):
    namespace: str
    action: Literal["ACTIVATE", "DISABLE"]


class EditDraftBody(_Body):
    expected_revision: int
    graph: list[dict[str, Any]]


@router.get("/domain-packs")
def list_domain_packs(ctx: TenantContext = require("domain_pack.read")) -> list[dict]:
    return store.list_packs(ctx)


@router.post("/domain-packs")
def manage_domain_packs(
    body: ManagePackBody, ctx: TenantContext = require("domain_pack.manage")
) -> dict:
    if body.action == "ACTIVATE":
        return store.activate_pack(ctx, body.namespace)
    return store.disable_pack(ctx, body.namespace)


@router.post("/ontology/generate")
def generate_ontology(ctx: TenantContext = require("ontology.generate")) -> None:
    raise not_implemented("2.2")


@router.post("/ontology/validate")
def validate_ontology(ctx: TenantContext = require("ontology.validate")) -> None:
    raise not_implemented("2.3")


@router.post("/ontology/publish")
def publish_ontology(ctx: TenantContext = require("ontology.publish")) -> None:
    raise not_implemented("2.7")


@router.get("/ontology")
def read_ontology(ctx: TenantContext = require("ontology.read")) -> dict:
    return store.read_active_ontology(ctx)


@router.post("/ontology/drafts", status_code=201)
def create_ontology_draft(ctx: TenantContext = require("ontology.edit")) -> dict:
    return store.create_draft(ctx)


@router.get("/ontology/drafts/open")
def read_ontology_draft(ctx: TenantContext = require("ontology.read")) -> dict:
    return store.read_open_draft(ctx)


@router.patch("/ontology/drafts/{draft_id}")
def edit_ontology_draft(
    draft_id: UUID, body: EditDraftBody, ctx: TenantContext = require("ontology.edit")
) -> dict:
    return store.edit_draft(ctx, draft_id, body.expected_revision, body.graph)
