"""Domain runs, cases and identity-resolution review (spec 7, 9.3)."""

from fastapi import APIRouter

from app.authz.guard import TenantContext, require
from app.errors import not_implemented

router = APIRouter(prefix="/api", tags=["domain"])

# TODO(G1 1.10 Suprava): add entity reads (spec 12.2 omits them), guarded by entity.read.


@router.post("/domain-runs")
def create_domain_run(ctx: TenantContext = require("domain_run.create")) -> None:
    raise not_implemented("3.4")


@router.get("/domain-cases")
def list_domain_cases(ctx: TenantContext = require("case.read")) -> None:
    raise not_implemented("3.4")


@router.patch("/domain-cases/{case_id}")
def triage_domain_case(case_id: str, ctx: TenantContext = require("case.triage")) -> None:
    raise not_implemented("3.4")


@router.post("/domain-cases/{case_id}/reopen")
def reopen_domain_case(case_id: str, ctx: TenantContext = require("case.triage")) -> None:
    raise not_implemented("3.4")


@router.post("/entity-resolution/{decision_id}/decision")
def review_entity_resolution(
    decision_id: str, ctx: TenantContext = require("entity_resolution.review")
) -> None:
    raise not_implemented("3.1")
