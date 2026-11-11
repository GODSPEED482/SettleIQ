"""LLM-drafted ontology proposals, grounded in discovered metadata (spec 5.7, EXT-REQ-09).

Model output is untrusted and only ever becomes reviewable proposals on a draft. It is never
copied into the tenant extension, so nothing generated can reach publication without a person
accepting it (AC-ONT-03). Binding a field to a meaning is always an inference; only evidence
the server can confirm against the metadata is labelled observed (AC-ONT-04).
"""

import json
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection, text

from app.audit import write_audit
from app.authz.guard import TenantContext
from app.db import tenant_tx
from app.errors import conflict, not_found


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DiscoveredField(_Strict):
    name: str
    datatype: str
    nullable: bool
    unique: bool
    profile: dict[str, Any] = {}


class DiscoveredForeignKey(_Strict):
    field: str
    references: str


class DiscoveredObject(_Strict):
    source_id: str
    object: str
    fields: list[DiscoveredField]
    foreign_keys: list[DiscoveredForeignKey] = []


class ProposedEvidence(_Strict):
    kind: Literal["foreign_key", "field_profile", "name_similarity"]
    field: str
    detail: str = Field(max_length=500)


class ProposedTerm(_Strict):
    id: str = Field(pattern=r"^tenant:[A-Za-z][A-Za-z0-9_]*$")
    kind: Literal["class", "property", "relationship"]
    label: str = Field(max_length=200)
    subclass_of: str | None
    domain: str | None
    range: str | None
    evidence: list[ProposedEvidence] = Field(min_length=1)


class ProposedBinding(_Strict):
    source_id: str
    object: str
    field: str
    property: str
    confidence: Literal["low", "medium", "high"]
    evidence: list[ProposedEvidence] = Field(min_length=1)


class OntologyProposal(_Strict):
    terms: list[ProposedTerm]
    bindings: list[ProposedBinding]


class UngroundedProposal(ValueError):
    pass


def _discovered_object(row: Any) -> DiscoveredObject:
    snapshot, profile = row.schema_snapshot, row.profile.get("columns", {})
    return DiscoveredObject(
        source_id=str(row.source_id),
        object=row.object_name,
        fields=[
            DiscoveredField(
                name=column["name"],
                datatype=column["datatype"],
                nullable=column["nullable"],
                unique=column["unique"],
                profile=profile.get(column["name"], {}),
            )
            for column in snapshot.get("columns", [])
        ],
        # Composite keys stay in the snapshot; a proposal cites one field as evidence.
        foreign_keys=[
            DiscoveredForeignKey(
                field=key["columns"][0],
                references=f"{key['references']['table']}.{key['references']['columns'][0]}",
            )
            for key in snapshot.get("foreign_keys", [])
            if len(key["columns"]) == 1
        ],
    )


def load_discovered_metadata(conn: Connection) -> list[DiscoveredObject]:
    """The tenant's discovered schema and profile aggregates, with no raw rows or secrets."""
    rows = conn.execute(
        text(
            """
            SELECT source_id, object_name, schema_snapshot, profile FROM source_objects
            WHERE schema_snapshot ? 'columns'
            ORDER BY source_id, object_name
            """
        )
    ).all()
    return [_discovered_object(row) for row in rows]


def _composition_terms(conn: Connection, composition: list[dict[str, str]]) -> list[str]:
    hashes = [reference["content_hash"] for reference in composition]
    rows = conn.execute(
        text("SELECT content FROM ontology_artifacts WHERE content_hash = ANY(:hashes)"),
        {"hashes": hashes},
    ).all()
    return sorted({node["@id"] for row in rows for node in row.content.get("@graph", [])})


def enqueue_generation(ctx: TenantContext) -> dict[str, str]:
    with tenant_tx(ctx) as conn:
        draft = conn.execute(
            text("SELECT id, composition FROM ontology_drafts WHERE status = 'OPEN' FOR UPDATE")
        ).first()
        if draft is None:
            raise conflict("NO_OPEN_DRAFT", "Create an ontology draft first.")
        metadata = load_discovered_metadata(conn)
        if not metadata:
            raise conflict("NO_DISCOVERED_METADATA", "Discover a source before generating.")
        in_flight = conn.execute(
            text(
                """
                SELECT 1 FROM agent_jobs
                WHERE kind = 'ONTOLOGY_DRAFT' AND subject_id = :draft
                  AND status IN ('QUEUED', 'RUNNING')
                """
            ),
            {"draft": draft.id},
        ).first()
        if in_flight is not None:
            raise conflict("GENERATION_IN_PROGRESS", "A draft is already being generated.")
        job_id = uuid4()
        payload = {
            "metadata": [item.model_dump() for item in metadata],
            "known_terms": _composition_terms(conn, draft.composition),
        }
        conn.execute(
            text(
                """
                INSERT INTO agent_jobs (id, tenant_id, kind, subject_id, requested_by, payload)
                VALUES (:id, :tenant, 'ONTOLOGY_DRAFT', :draft, :user, CAST(:payload AS jsonb))
                """
            ),
            {
                "id": job_id,
                "tenant": ctx.tenant_id,
                "draft": draft.id,
                "user": ctx.principal.id,
                "payload": json.dumps(payload),
            },
        )
        write_audit(
            conn, ctx.tenant_id, ctx.principal, "ontology.generation.requested", "ontology",
            {"draft_id": str(draft.id), "job_id": str(job_id)},
        )
    return {"job_id": str(job_id), "status": "QUEUED"}


def read_generation_job(ctx: TenantContext, job_id: UUID) -> dict[str, Any]:
    with tenant_tx(ctx) as conn:
        row = conn.execute(
            text(
                """
                SELECT id, status, error, created_at, finished_at FROM agent_jobs
                WHERE id = :id AND kind = 'ONTOLOGY_DRAFT'
                """
            ),
            {"id": job_id},
        ).first()
    if row is None:
        raise not_found()
    return {
        "job_id": str(row.id),
        "status": row.status,
        "error": row.error,
        "created_at": row.created_at.isoformat(),
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }


_OBSERVED_DETAIL = {
    "foreign_key": "{field} is a foreign key in the discovered metadata.",
    "field_profile": "{field} is a field in the discovered metadata.",
}


def _label_evidence(
    evidence: tuple[ProposedEvidence, ...] | list[ProposedEvidence],
    fields: set[str],
    foreign_keys: set[str],
) -> list[dict[str, str]]:
    labelled = []
    for item in evidence:
        if item.kind == "foreign_key" and item.field not in foreign_keys:
            raise UngroundedProposal(f"no foreign key on {item.field}")
        if item.kind != "name_similarity" and item.field not in fields:
            raise UngroundedProposal(f"unknown field {item.field}")
        if item.kind == "name_similarity":
            labelled.append(item.model_dump() | {"basis": "inferred"})
            continue
        # The server confirmed the field, not the model's reading of it, so that text is kept
        # apart from what is labelled observed.
        labelled.append(
            {
                "kind": item.kind,
                "field": item.field,
                "detail": _OBSERVED_DETAIL[item.kind].format(field=item.field),
                "rationale": item.detail,
                "basis": "observed",
            }
        )
    return labelled


def ground_proposal(
    proposal: OntologyProposal, metadata: list[DiscoveredObject], known_terms: list[str]
) -> list[dict[str, Any]]:
    """Turn model output into reviewable proposals, or refuse anything the input cannot support."""
    objects = {(item.source_id, item.object): item for item in metadata}
    all_fields = {field.name for item in metadata for field in item.fields}
    all_foreign_keys = {key.field for item in metadata for key in item.foreign_keys}
    allowed = set(known_terms) | {term.id for term in proposal.terms}

    grounded: list[dict[str, Any]] = []
    for term in proposal.terms:
        for reference in (term.subclass_of, term.domain, term.range):
            if reference is not None and reference not in allowed:
                raise UngroundedProposal(f"unknown term {reference}")
        grounded.append(
            term.model_dump(exclude={"evidence"})
            | {
                "type": "term",
                "status": "PROPOSED",
                "basis": "inferred",
                "evidence": _label_evidence(term.evidence, all_fields, all_foreign_keys),
            }
        )
    for binding in proposal.bindings:
        target = objects.get((binding.source_id, binding.object))
        if target is None or binding.field not in {field.name for field in target.fields}:
            raise UngroundedProposal(f"unknown field {binding.object}.{binding.field}")
        if binding.property not in allowed:
            raise UngroundedProposal(f"unknown property {binding.property}")
        grounded.append(
            binding.model_dump(exclude={"evidence"})
            | {
                "type": "binding",
                "status": "PROPOSED",
                "basis": "inferred",
                "evidence": _label_evidence(
                    binding.evidence,
                    {field.name for field in target.fields},
                    {key.field for key in target.foreign_keys},
                ),
            }
        )
    return grounded
