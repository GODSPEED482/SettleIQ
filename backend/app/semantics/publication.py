"""Atomic, immutable ontology publication (POL-12, spec 5.7, ADR-018).

Every check runs under the locks that stop the candidate changing, in the transaction that
writes the new version. The draft is locked before the tenant: edits lock the draft and then
reach the tenant row through foreign keys, so the opposite order could deadlock with them.
"""

import json
from typing import Any
from uuid import UUID, uuid4

from rdflib import OWL, RDF, RDFS
from sqlalchemy import Connection, text

from app.audit import write_audit
from app.authz.guard import TenantContext
from app.db import tenant_tx
from app.domain_packs.manifest import load_domain_packs
from app.errors import ApiError, conflict, not_found
from app.semantics import store, validation
from app.semantics.validation import Candidate


def _lock_open_draft(conn: Connection, draft_id: UUID) -> int:
    row = conn.execute(
        text("SELECT status, base_version FROM ontology_drafts WHERE id = :id FOR UPDATE"),
        {"id": draft_id},
    ).first()
    if row is None:
        raise not_found()
    if row.status != "OPEN":
        raise conflict("STALE_DRAFT", "This draft is no longer open.")
    return row.base_version


def _require_passing_run(
    conn: Connection, candidate: Candidate, run_id: UUID
) -> list[dict[str, Any]]:
    run = conn.execute(
        text(
            """
            SELECT passed, content_hash, draft_revision, results
            FROM ontology_validation_runs WHERE id = :id AND draft_id = :draft
            """
        ),
        {"id": run_id, "draft": candidate.draft_id},
    ).first()
    if run is None:
        raise not_found()
    if not run.passed:
        raise conflict("VALIDATION_NOT_PASSED", "This validation run did not pass.")
    if run.content_hash != candidate.content_hash or run.draft_revision != candidate.revision:
        raise conflict("VALIDATION_STALE", "The draft changed. Validate it again.")
    return run.results


def _require_current_packs(
    conn: Connection, candidate: Candidate, results: list[dict[str, Any]]
) -> None:
    """Changing the active packs does not change the hash, so check them separately.

    A pack with validators but no ontology content leaves the composition as it was, so the
    run itself must show that each active pack's validators passed.
    """
    packs = load_domain_packs()
    active = store.active_namespaces(conn)
    stale = conflict("VALIDATION_STALE", "Active packs changed. Validate again.")
    if any(name not in packs for name in active):
        raise stale
    if not validation.composition_is_current(candidate, active, packs):
        raise stale
    required = {
        item.id
        for name in active
        for item in [*packs[name].shacl_shapes, *packs[name].validators]
    }
    if not required <= {result["validator"] for result in results if result["passed"]}:
        raise stale


def _insert_relationships(
    conn: Connection, tenant_id: UUID, ontology_id: UUID, candidate: Candidate
) -> int:
    """SHACL has already held every relationship to one label, one domain and one range."""
    graph = validation.candidate_graph(candidate)
    relationships = sorted(set(graph.subjects(RDF.type, OWL.ObjectProperty)))
    for iri in relationships:
        conn.execute(
            text(
                """
                INSERT INTO ontology_relationships
                    (id, tenant_id, ontology_id, iri, domain_iri, range_iri, definition)
                VALUES (:id, :tenant, :ontology, :iri, :domain, :range,
                    CAST(:definition AS jsonb))
                """
            ),
            {
                "id": uuid4(),
                "tenant": tenant_id,
                "ontology": ontology_id,
                "iri": str(iri),
                "domain": str(graph.value(iri, RDFS.domain)),
                "range": str(graph.value(iri, RDFS.range)),
                "definition": json.dumps({"label": str(graph.value(iri, RDFS.label))}),
            },
        )
    return len(relationships)


def _publish(ctx: TenantContext, draft_id: UUID, run_id: UUID) -> dict[str, Any]:
    with tenant_tx(ctx) as conn:
        base_version = _lock_open_draft(conn, draft_id)
        store.lock_tenant(conn, ctx.tenant_id)
        candidate = validation.load_candidate(conn, draft_id)
        results = _require_passing_run(conn, candidate, run_id)
        _require_current_packs(conn, candidate, results)
        latest = store.latest_version(conn)
        if base_version != latest:
            raise conflict("STALE_BASE_VERSION", "A newer version exists. Start a new draft.")

        ontology_id = uuid4()
        version = latest + 1
        published_at = conn.execute(
            text(
                """
                INSERT INTO ontologies (id, tenant_id, version, composition, tenant_extension,
                    content_hash, draft_id, published_by)
                VALUES (:id, :tenant, :version, CAST(:composition AS jsonb),
                    CAST(:extension AS jsonb), :hash, :draft, :user)
                RETURNING published_at
                """
            ),
            {
                "id": ontology_id,
                "tenant": ctx.tenant_id,
                "version": version,
                "composition": json.dumps(candidate.composition),
                "extension": json.dumps(candidate.tenant_extension),
                "hash": candidate.content_hash,
                "draft": draft_id,
                "user": ctx.principal.id,
            },
        ).scalar_one()
        relationships = _insert_relationships(conn, ctx.tenant_id, ontology_id, candidate)
        conn.execute(
            text(
                "UPDATE ontology_drafts SET status = 'PUBLISHED', updated_at = now() "
                "WHERE id = :id"
            ),
            {"id": draft_id},
        )
        write_audit(
            conn, ctx.tenant_id, ctx.principal, "ontology.published", "ontology",
            {
                "outcome": "PUBLISHED",
                "ontology_id": str(ontology_id),
                "composition": candidate.composition,
                "draft_id": str(draft_id),
                "draft_revision": candidate.revision,
                "base_version": base_version,
                "version": version,
                "content_hash": candidate.content_hash,
                "validation_run_id": str(run_id),
                "publisher": str(ctx.principal.id),
                "confirmed_at": published_at.isoformat(),
            },
        )
    return {
        "id": str(ontology_id),
        "version": version,
        "content_hash": candidate.content_hash,
        "validation_run_id": str(run_id),
        "published_at": published_at.isoformat(),
        "relationships": relationships,
    }


def _audit_refusal(ctx: TenantContext, draft_id: UUID, run_id: UUID, code: str) -> None:
    with tenant_tx(ctx) as conn:
        write_audit(
            conn, ctx.tenant_id, ctx.principal, "ontology.publish.refused", "ontology",
            {
                "outcome": "REFUSED",
                "reason": code,
                "draft_id": str(draft_id),
                "validation_run_id": str(run_id),
                "publisher": str(ctx.principal.id),
            },
        )


def publish(ctx: TenantContext, draft_id: UUID, run_id: UUID) -> dict[str, Any]:
    try:
        return _publish(ctx, draft_id, run_id)
    except ApiError as refusal:
        # The refused transaction rolled back, so its outcome is recorded in a separate one.
        if refusal.status == 409:
            _audit_refusal(ctx, draft_id, run_id, refusal.code)
        raise
