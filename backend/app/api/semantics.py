"""Domain packs and the ontology lifecycle (spec 5.7, POL-12)."""

from fastapi import APIRouter

from app.authz.guard import TenantContext, require
from app.errors import not_implemented

router = APIRouter(prefix="/api", tags=["semantics"])


@router.get("/domain-packs")
def list_domain_packs(ctx: TenantContext = require("domain_pack.read")) -> None:
    raise not_implemented("2.1")


@router.post("/domain-packs")
def manage_domain_packs(ctx: TenantContext = require("domain_pack.manage")) -> None:
    raise not_implemented("2.1")


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
def read_ontology(ctx: TenantContext = require("ontology.read")) -> None:
    raise not_implemented("2.1")
