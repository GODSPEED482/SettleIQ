"""Domain runs, cases and identity-resolution review (spec 7, 9.3)."""

from fastapi import APIRouter

from app.authz.guard import TenantContext, require
from app.errors import not_implemented

router = APIRouter(prefix="/api", tags=["domain"])

# TODO(G3 exit Swapnaneel): identical version-pinned inputs give identical findings across
# runs; fees or refunds already included in net are never subtracted again (AC-REC-07); an
# ambiguous identity match is never forced (AC-REC-06). Each needs an automated test.


# TODO(G3 3.4 Swapnaneel): insert a `domain_runs` row pinning the domain-pack version, the
# published ontology version, source snapshots and mapping and rule versions (spec 6.3). Run the
# active pack's domain_engine over those pinned inputs only, and write `domain_cases` that carry
# case type, reconciliation scope, severity, affected entities and observations, evidence and
# the run's version references (AC-REC-08). The same inputs must give the same cases.
# TODO(G3 3.4 Swapnaneel): spec 12.2 has no run read, but the Domain Operations screen (3.9)
# needs one. Add GET /api/domain-runs under domain_run.read, as 1.10 did for entity reads, and
# regenerate openapi.json.
@router.post("/domain-runs")
def create_domain_run(ctx: TenantContext = require("domain_run.create")) -> None:
    raise not_implemented("3.4")


@router.get("/domain-cases")
def list_domain_cases(ctx: TenantContext = require("case.read")) -> None:
    raise not_implemented("3.4")


# TODO(G3 3.4 Swapnaneel): case lifecycle as its own state machine (spec 9.3): OPEN,
# INVESTIGATING, HUMAN_REVIEW, ACTION_PENDING, RESOLVED, plus DISMISSED and CLOSED. Dismiss,
# close and reopen need a reason and an audit event, and reopen applies only to CLOSED
# (AC-CASE-01). Refuse dismiss or close in ACTION_PENDING (AC-CASE-02). Never accept RESOLVED
# here: only verified readback resolves a case. Each handler that starts touching the database
# needs test_held_permission_passes_the_gate adjusted (200 or 501 only).
@router.patch("/domain-cases/{case_id}")
def triage_domain_case(case_id: str, ctx: TenantContext = require("case.triage")) -> None:
    raise not_implemented("3.4")


@router.post("/domain-cases/{case_id}/reopen")
def reopen_domain_case(case_id: str, ctx: TenantContext = require("case.triage")) -> None:
    raise not_implemented("3.4")


# Spec 12.2 omits entity reads; task 1.10 adds them under entity.read.
# TODO(G3 3.1 Swapnaneel): project source_records the active pack recognizes into
# `domain_entities`, linked through `entity_source_links`. Links come only from recorded
# `entity_resolution_decisions` (AC-REC-11). Match on exact shared identifiers only (AC-REC-03).
# Two or more candidates stay unresolved and reviewable, never force-attached (AC-REC-06).
# Records no pack recognizes stay preserved and unlinked (spec 7.6).
# TODO(G3 3.2 Swapnaneel): write one append-only `property_observations` row per mapped field of
# each linked record, with raw and normalized values, datatype, currency, event and observed
# time, evidence and mapping version (spec 5.5). Two bindings to one property give two
# observations (AC-REC-10). Derive `resolved_property_views` from the published authority
# assertions: RESOLVED only under one applicable authority, otherwise CONFLICT, UNRESOLVED,
# STALE or HUMAN_REVIEW. Keep every contributing observation ID; never choose a value by ingest
# order (AC-REC-12, AC-REC-16). Return both views from the entity read.
@router.get("/entities")
def list_entities(ctx: TenantContext = require("entity.read")) -> None:
    raise not_implemented("3.1")


@router.get("/entities/{entity_id}")
def read_entity(entity_id: str, ctx: TenantContext = require("entity.read")) -> None:
    raise not_implemented("3.1")


# TODO(G3 3.1 Swapnaneel): accept or reject one candidate of a pending decision. Record the
# reviewer, time and reason, then create the link only on accept. Audit both outcomes.
@router.post("/entity-resolution/{decision_id}/decision")
def review_entity_resolution(
    decision_id: str, ctx: TenantContext = require("entity_resolution.review")
) -> None:
    raise not_implemented("3.1")
