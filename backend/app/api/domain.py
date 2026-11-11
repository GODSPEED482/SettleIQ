"""Domain runs, cases and identity-resolution review (spec 7, 9.3)."""

from uuid import UUID

from fastapi import APIRouter

from app.authz.guard import TenantContext, require
from app.domain import cases, entities, runs

router = APIRouter(prefix="/api", tags=["domain"])

# G3 exit evidence (determinism, AC-REC-07, AC-REC-06) is in tests/test_reconciliation.py.


@router.post("/domain-runs", status_code=201)
def create_domain_run(
    body: runs.RunBody | None = None, ctx: TenantContext = require("domain_run.create")
) -> dict:
    return runs.start_run(ctx, body or runs.RunBody())


# Spec 12.2 has no run read; the Domain Operations screen (3.9) needs one, as 1.10 added
# entity reads.
@router.get("/domain-runs")
def list_domain_runs(ctx: TenantContext = require("domain_run.read")) -> list[dict]:
    return runs.list_runs(ctx)


@router.get("/domain-runs/{run_id}")
def read_domain_run(run_id: UUID, ctx: TenantContext = require("domain_run.read")) -> dict:
    return runs.read_run(ctx, run_id)


@router.get("/domain-cases")
def list_domain_cases(
    status: str | None = None,
    case_type: str | None = None,
    run_id: UUID | None = None,
    ctx: TenantContext = require("case.read"),
) -> list[dict]:
    return cases.list_cases(ctx, status, case_type, run_id)


@router.patch("/domain-cases/{case_id}")
def triage_domain_case(
    case_id: UUID, body: cases.TriageBody, ctx: TenantContext = require("case.triage")
) -> dict:
    return cases.triage(ctx, case_id, body)


@router.post("/domain-cases/{case_id}/reopen")
def reopen_domain_case(
    case_id: UUID, body: cases.ReopenBody, ctx: TenantContext = require("case.triage")
) -> dict:
    return cases.reopen(ctx, case_id, body)


# Spec 12.2 omits entity reads; task 1.10 adds them under entity.read.
@router.get("/entities")
def list_entities(
    class_iri: str | None = None, ctx: TenantContext = require("entity.read")
) -> list[dict]:
    return entities.list_entities(ctx, class_iri)


@router.get("/entities/{entity_id}")
def read_entity(entity_id: UUID, ctx: TenantContext = require("entity.read")) -> dict:
    return entities.read_entity(ctx, entity_id)


# The review queue for the Domain Operations screen (3.9), under entity_resolution.read.
@router.get("/entity-resolution")
def list_entity_resolution(
    status: str | None = None, ctx: TenantContext = require("entity_resolution.read")
) -> list[dict]:
    return entities.list_decisions(ctx, status)


@router.post("/entity-resolution/{decision_id}/decision")
def review_entity_resolution(
    decision_id: UUID,
    body: entities.ReviewBody,
    ctx: TenantContext = require("entity_resolution.review"),
) -> dict:
    return entities.review(ctx, decision_id, body)
