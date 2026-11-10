"""Hash-bound ontology validation (POL-12, spec 5.7).

The candidate is hashed, then SHACL and every active pack's validators run against exactly that
content. Any edit changes the hash, so an earlier run stops matching the candidate. Validators
fail closed: an error inside one counts as a failure of that validator.
"""

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from pyshacl import validate as run_pyshacl
from rdflib import Graph, Namespace
from sqlalchemy import Connection, text

from app.audit import write_audit
from app.authz.guard import TenantContext
from app.db import tenant_tx
from app.domain_packs.manifest import PACKS_DIR, DomainPackManifest, load_domain_packs
from app.domain_packs.registry import resolve_entrypoint
from app.errors import conflict, not_found
from app.semantics import mappings
from app.semantics.hashing import content_hash

# TODO(G2 2.3 Swapnaneel): owner review of the candidate hash, SHACL runs and pack validators.

logger = logging.getLogger("settleiq.semantics")

CORE_SHAPES = Path(__file__).with_name("core.shacl.ttl")
SH = Namespace("http://www.w3.org/ns/shacl#")
MAX_MESSAGES = 50


@dataclass(frozen=True)
class ValidatorResult:
    validator: str
    kind: str
    passed: bool
    messages: list[str]


@dataclass(frozen=True)
class Candidate:
    draft_id: UUID
    revision: int
    composition: list[dict[str, str]]
    artifacts: list[dict[str, Any]]
    tenant_extension: dict[str, Any]
    mappings: list[dict[str, Any]]
    authority: list[dict[str, Any]]
    content_hash: str


def candidate_content(
    composition: list[dict[str, str]],
    tenant_extension: dict[str, Any],
    mapping_rows: list[dict[str, Any]],
    authority_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Exactly what the publication hash covers."""
    return {
        "composition": composition,
        "tenant_extension": tenant_extension,
        "mappings": mappings.candidate_mappings(mapping_rows),
        "authority": mappings.candidate_authority(authority_rows),
    }


def load_candidate(conn: Connection, draft_id: UUID) -> Candidate:
    row = conn.execute(
        text(
            """
            SELECT id, revision, status, composition, tenant_extension
            FROM ontology_drafts WHERE id = :id
            """
        ),
        {"id": draft_id},
    ).first()
    if row is None:
        raise not_found()
    if row.status != "OPEN":
        raise conflict("STALE_DRAFT", "This draft is no longer open.")
    rows = conn.execute(
        text("SELECT content_hash, content FROM ontology_artifacts WHERE content_hash = ANY(:h)"),
        {"h": [reference["content_hash"] for reference in row.composition]},
    ).all()
    contents = {item.content_hash: item.content for item in rows}
    artifacts = [ref | {"content": contents[ref["content_hash"]]} for ref in row.composition]
    mapping_rows = mappings.current_mappings(conn, row.id)
    authority_rows = mappings.current_authority(conn, row.id)
    return Candidate(
        row.id,
        row.revision,
        row.composition,
        artifacts,
        row.tenant_extension,
        mapping_rows,
        authority_rows,
        content_hash(
            candidate_content(row.composition, row.tenant_extension, mapping_rows, authority_rows)
        ),
    )


def _candidate_graph(candidate: Candidate) -> Graph:
    graph = Graph()
    for artifact in candidate.artifacts:
        graph.parse(data=json.dumps(artifact["content"]), format="json-ld")
    graph.parse(data=json.dumps(candidate.tenant_extension), format="json-ld")
    return graph


def _known_terms(candidate: Candidate) -> set[str]:
    sources = [artifact["content"] for artifact in candidate.artifacts]
    return {node["@id"] for source in [*sources, candidate.tenant_extension]
            for node in source.get("@graph", [])}


def _mapping_results(candidate: Candidate) -> list[ValidatorResult]:
    comparable = mappings.comparability_violations(candidate.mappings)
    unknown = mappings.unknown_term_violations(
        candidate.mappings, candidate.authority, _known_terms(candidate)
    )
    return [
        ValidatorResult("mappings.comparable", "platform", not comparable, comparable),
        ValidatorResult("mappings.terms_exist", "platform", not unknown, unknown),
    ]


def _run_shacl(name: str, data: Graph, shapes_path: Path) -> ValidatorResult:
    try:
        shapes = Graph().parse(shapes_path, format="turtle")
        conforms, report, _ = run_pyshacl(data, shacl_graph=shapes, inference="none")
    except Exception:
        logger.exception("SHACL run failed: %s", name)
        return ValidatorResult(name, "shacl", False, ["SHACL validation could not complete."])
    messages = sorted(
        {
            f"{report.value(result, SH.focusNode)}: {report.value(result, SH.resultMessage)}"
            for result in report.subjects(SH.resultSeverity, None)
        }
    )
    return ValidatorResult(name, "shacl", bool(conforms), messages[:MAX_MESSAGES])


def _run_pack_validator(identifier: str, entrypoint: str, data: Graph) -> ValidatorResult:
    try:
        messages = list(resolve_entrypoint(entrypoint)(data))
    except Exception:
        logger.exception("pack validator failed: %s", identifier)
        return ValidatorResult(identifier, "pack", False, ["The validator could not complete."])
    return ValidatorResult(identifier, "pack", not messages, messages[:MAX_MESSAGES])


def _pack_results(pack: DomainPackManifest, data: Graph) -> list[ValidatorResult]:
    results = [
        _run_shacl(item.id, data, PACKS_DIR / pack.package / item.path)
        for item in pack.shacl_shapes
    ]
    results += [_run_pack_validator(item.id, item.entrypoint, data) for item in pack.validators]
    return results


def run_validators(
    candidate: Candidate, active: list[str], packs: dict[str, DomainPackManifest]
) -> list[ValidatorResult]:
    composed = sorted(r["namespace"] for r in candidate.composition if r["layer"] == "PACK")
    # Packs with no ontology content contribute no artifact, so only those with content must match.
    expected = sorted(name for name in active if name in packs and packs[name].ontology_definitions)
    results = [
        ValidatorResult(
            "composition.current",
            "platform",
            composed == expected,
            [] if composed == expected else ["Active packs changed. Start a new draft."],
        ),
        *_mapping_results(candidate),
    ]
    try:
        data = _candidate_graph(candidate)
    except Exception:
        logger.exception("candidate did not parse")
        return [*results, ValidatorResult("candidate.parse", "platform", False,
                                          ["The candidate is not valid JSON-LD."])]
    results.append(_run_shacl("core.shapes", data, CORE_SHAPES))
    for name in sorted(set(active) | set(composed)):
        if name not in packs:
            results.append(
                ValidatorResult(name, "platform", False, ["This pack is not installed."])
            )
            continue
        results.extend(_pack_results(packs[name], data))
    return results


def validate_draft(ctx: TenantContext, draft_id: UUID) -> dict[str, Any]:
    with tenant_tx(ctx) as conn:
        candidate = load_candidate(conn, draft_id)
        active = list(
            conn.execute(
                text("SELECT namespace FROM tenant_domain_packs WHERE status = 'ACTIVE'")
            ).scalars()
        )
    # Validation can take seconds, so it holds no transaction. The hash binds the result.
    results = run_validators(candidate, active, load_domain_packs())
    passed = all(result.passed for result in results)
    run_id = uuid4()
    payload = [asdict(result) for result in results]
    with tenant_tx(ctx) as conn:
        conn.execute(
            text(
                """
                INSERT INTO ontology_validation_runs (id, tenant_id, draft_id, draft_revision,
                    content_hash, passed, results, validated_by)
                VALUES (:id, :tenant, :draft, :revision, :hash, :passed,
                    CAST(:results AS jsonb), :user)
                """
            ),
            {
                "id": run_id,
                "tenant": ctx.tenant_id,
                "draft": candidate.draft_id,
                "revision": candidate.revision,
                "hash": candidate.content_hash,
                "passed": passed,
                "results": json.dumps(payload),
                "user": ctx.principal.id,
            },
        )
        write_audit(
            conn, ctx.tenant_id, ctx.principal, "ontology.validated", "ontology",
            {
                "draft_id": str(candidate.draft_id),
                "validation_run_id": str(run_id),
                "content_hash": candidate.content_hash,
                "passed": passed,
            },
        )
    return {
        "validation_run_id": str(run_id),
        "draft_id": str(candidate.draft_id),
        "revision": candidate.revision,
        "content_hash": candidate.content_hash,
        "passed": passed,
        "results": payload,
    }
