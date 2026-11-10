"""Domain packs and the ontology lifecycle (spec 5.7, POL-12)."""

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from app.authz.guard import TenantContext, require
from app.errors import not_implemented
from app.semantics import generation, mappings, store, validation

router = APIRouter(prefix="/api", tags=["semantics"])

class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ManagePackBody(_Body):
    namespace: str
    action: Literal["ACTIVATE", "DISABLE"]


class ValidateBody(_Body):
    draft_id: UUID


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


@router.post("/ontology/generate", status_code=202)
def generate_ontology(ctx: TenantContext = require("ontology.generate")) -> dict:
    return generation.enqueue_generation(ctx)


@router.get("/ontology/generation-jobs/{job_id}")
def read_generation_job(job_id: UUID, ctx: TenantContext = require("ontology.read")) -> dict:
    return generation.read_generation_job(ctx, job_id)


@router.post("/ontology/validate")
def validate_ontology(
    body: ValidateBody, ctx: TenantContext = require("ontology.validate")
) -> dict:
    return validation.validate_draft(ctx, body.draft_id)


# TODO(G2 2.3 Swapnaneel): take a body of draft_id, validation_run_id and confirm_publish, and
# refuse anything but confirm_publish=true, even with ontology.publish (AC-ONTO-PUB-01).
# TODO(G2 2.7 Rishav): in one tenant_tx, lock the tenant, reload the candidate with
# validation.load_candidate and require the named run to have passed for that exact hash and
# revision (AC-ONTO-PUB-02, 03). Require the draft's base_version to still be the latest version,
# so a stale or concurrent publish fails closed and never overwrites one (AC-ONTO-PUB-05). Then
# insert the next `ontologies` row and its `ontology_relationships`, mark the draft PUBLISHED,
# and audit tenant, versions, hash, validation run, publisher and outcome (spec 5.7 step 6,
# AC-ONTO-PUB-04). The new body changes the frozen contract: regenerate openapi.json, and adjust
# test_held_permission_passes_the_gate, which accepts only 200 or 501.
@router.post("/ontology/publish")
def publish_ontology(ctx: TenantContext = require("ontology.publish")) -> None:
    raise not_implemented("2.7")


@router.get("/ontology")
def read_ontology(ctx: TenantContext = require("ontology.read")) -> dict:
    return store.read_active_ontology(ctx)


@router.post("/ontology/drafts", status_code=201)
def create_ontology_draft(ctx: TenantContext = require("ontology.edit")) -> dict:
    return store.create_draft(ctx)


@router.get("/mappings")
def list_mappings(draft_id: UUID, ctx: TenantContext = require("mapping.read")) -> list[dict]:
    return mappings.list_mappings(ctx, draft_id)


@router.post("/mappings", status_code=201)
def save_mapping(
    body: mappings.MappingBody, ctx: TenantContext = require("mapping.edit")
) -> dict:
    return mappings.save_mapping(ctx, body)


@router.post("/mappings/{mapping_id}/retire")
def retire_mapping(mapping_id: UUID, ctx: TenantContext = require("mapping.edit")) -> dict:
    return mappings.retire_mapping(ctx, mapping_id)


@router.get("/authority")
def list_authority(draft_id: UUID, ctx: TenantContext = require("authority.read")) -> list[dict]:
    return mappings.list_authority(ctx, draft_id)


@router.post("/authority", status_code=201)
def save_authority(
    body: mappings.AuthorityBody, ctx: TenantContext = require("authority.edit")
) -> dict:
    return mappings.save_authority(ctx, body)


@router.post("/authority/{assertion_id}/retire")
def retire_authority(assertion_id: UUID, ctx: TenantContext = require("authority.edit")) -> dict:
    return mappings.retire_authority(ctx, assertion_id)


@router.get("/ontology/drafts/open")
def read_ontology_draft(ctx: TenantContext = require("ontology.read")) -> dict:
    return store.read_open_draft(ctx)


@router.post("/ontology/drafts/{draft_id}/abandon")
def abandon_ontology_draft(draft_id: UUID, ctx: TenantContext = require("ontology.edit")) -> dict:
    return store.abandon_draft(ctx, draft_id)


@router.patch("/ontology/drafts/{draft_id}")
def edit_ontology_draft(
    draft_id: UUID, body: EditDraftBody, ctx: TenantContext = require("ontology.edit")
) -> dict:
    return store.edit_draft(ctx, draft_id, body.expected_revision, body.graph)
