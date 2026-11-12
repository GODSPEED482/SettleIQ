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
from app.domain.inputs import load_policy
from app.domain_packs.manifest import load_domain_packs
from app.errors import conflict, not_found
from app.semantics.store import active_namespaces


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


ValueKind = Literal["identifier", "status", "money", "timestamp", "code", "text"]

# How each kind of value is read and compared. Bindings to one property must agree on these
# (comparability_violations), so they are fixed here rather than chosen by the model; they follow
# the conventions of the packs' own mapping hints.
_VALUE_KINDS: dict[str, dict[str, str]] = {
    "identifier": {"datatype": "string", "transform": "identity", "value_basis": "identifier",
                   "temporal_context": "any"},
    "status": {"datatype": "string", "transform": "upper", "value_basis": "status",
               "temporal_context": "current"},
    "money": {"datatype": "decimal", "transform": "decimal_string", "value_basis": "signed",
              "temporal_context": "at_event"},
    "timestamp": {"datatype": "datetime", "transform": "iso8601", "value_basis": "timestamp",
                  "temporal_context": "at_event"},
    "code": {"datatype": "string", "transform": "upper", "value_basis": "code",
             "temporal_context": "any"},
    "text": {"datatype": "string", "transform": "identity", "value_basis": "text",
             "temporal_context": "any"},
}


class ProposedBinding(_Strict):
    source_id: str
    object: str
    field: str
    property: str
    class_iri: str
    value_kind: ValueKind
    currency_field: str | None
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


_KINDS = {"owl:Class": "class", "owl:DatatypeProperty": "property",
          "owl:ObjectProperty": "relationship"}


def _composition_terms(
    conn: Connection, composition: list[dict[str, str]]
) -> list[dict[str, str | None]]:
    """Each composed term with what a model needs to choose it: kind, label, domain, range."""
    hashes = [reference["content_hash"] for reference in composition]
    rows = conn.execute(
        text("SELECT content FROM ontology_artifacts WHERE content_hash = ANY(:hashes)"),
        {"hashes": hashes},
    ).all()
    nodes = {node["@id"]: node for row in rows for node in row.content.get("@graph", [])}
    return [
        {
            "id": term_id,
            "kind": _KINDS.get(str(node.get("@type")), "term"),
            "label": node.get("rdfs:label"),
            "domain": node.get("rdfs:domain"),
            "range": node.get("rdfs:range"),
        }
        for term_id, node in sorted(nodes.items())
    ]


def _resolution(namespaces: list[str]) -> dict[str, Any]:
    """The active packs' exact-ID rules (spec 7.2): the properties whose value identifies an
    entity of each class, and the links made between entities sharing a value. Records from
    different sources describe one entity only through these, so a binding meant to be compared
    across sources, or linked to another entity, has to use them."""
    packs = load_domain_packs()
    identities: dict[str, list[str]] = {}
    relationships: list[dict[str, str]] = []
    for namespace in sorted(namespaces):
        if namespace in packs:
            policy = load_policy(packs[namespace])
            identities |= policy.identities
            relationships += policy.relationships
    return {"identities": dict(sorted(identities.items())), "relationships": relationships}


def enqueue_generation(ctx: TenantContext) -> dict[str, str]:
    with tenant_tx(ctx) as conn:
        draft = conn.execute(
            text("SELECT id, composition FROM ontology_drafts WHERE status = 'OPEN' FOR UPDATE")
        ).first()
        if draft is None:
            raise conflict("NO_OPEN_DRAFT", "Create an ontology draft first.")
        # The model may use only terms the draft composes. A draft started before a pack was
        # activated lacks that pack's classes and relationships, so its proposals would rebuild
        # them as generic tenant terms instead of binding to them.
        composed = {ref["namespace"] for ref in draft.composition if ref.get("layer") == "PACK"}
        missing = sorted(set(active_namespaces(conn)) - composed)
        if missing:
            raise conflict(
                "DRAFT_MISSING_PACKS",
                f"The open draft was started before {', '.join(missing)} was activated. "
                "Abandon it and start a new draft, then generate.",
            )
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
        details = _composition_terms(conn, draft.composition)
        payload = {
            "metadata": [item.model_dump() for item in metadata],
            "known_terms": [term["id"] for term in details],
            "term_details": details,
            "resolution": _resolution(active_namespaces(conn)),
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
    """`fields` and `foreign_keys` hold each name both bare and as `object.field`, so a model
    may cite either form; a qualified name matches only the object it names."""
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


def _names(item: DiscoveredObject, names: list[str]) -> set[str]:
    return {*names, *(f"{item.object}.{name}" for name in names)}


def _field_names(item: DiscoveredObject) -> set[str]:
    return _names(item, [field.name for field in item.fields])


def _foreign_key_names(item: DiscoveredObject) -> set[str]:
    return _names(item, [key.field for key in item.foreign_keys])


def _target(binding: ProposedBinding, metadata: list[DiscoveredObject]) -> DiscoveredObject:
    """The discovered object a binding names. A wrong source ID is tolerated only when the
    object name identifies exactly one discovered object; anything else is refused."""
    exact = [i for i in metadata if (i.source_id, i.object) == (binding.source_id, binding.object)]
    named = exact or [item for item in metadata if item.object == binding.object]
    if len(named) != 1:
        raise UngroundedProposal(f"unknown field {binding.object}.{binding.field}")
    return named[0]


def _ground_term(
    term: ProposedTerm, allowed: set[str], fields: set[str], foreign_keys: set[str]
) -> dict[str, Any]:
    for reference in (term.subclass_of, term.domain, term.range):
        if reference is not None and reference not in allowed:
            raise UngroundedProposal(f"unknown term {reference}")
    return term.model_dump(exclude={"evidence"}) | {
        "type": "term",
        "status": "PROPOSED",
        "basis": "inferred",
        "evidence": _label_evidence(term.evidence, fields, foreign_keys),
    }


def _ground_binding(
    binding: ProposedBinding, metadata: list[DiscoveredObject], allowed: set[str]
) -> dict[str, Any]:
    target = _target(binding, metadata)
    fields = {item.name: item for item in target.fields}
    field = binding.field.removeprefix(f"{target.object}.")
    if field not in fields:
        raise UngroundedProposal(f"unknown field {binding.object}.{binding.field}")
    if binding.property not in allowed:
        raise UngroundedProposal(f"unknown property {binding.property}")
    if binding.class_iri not in allowed:
        raise UngroundedProposal(f"unknown class {binding.class_iri}")
    currency = None
    if binding.value_kind == "money":
        currency = (binding.currency_field or "").removeprefix(f"{target.object}.")
        if currency not in fields:
            raise UngroundedProposal(f"no currency field for {binding.object}.{field}")
    kind = _VALUE_KINDS[binding.value_kind]
    # A complete mapping, so accepting the proposal is one action. It stays a proposal: the
    # mapping route validates it again when a person accepts it.
    mapping = {
        "class_iri": binding.class_iri,
        "datatype": kind["datatype"],
        "transform": {"name": kind["transform"]},
        "value_basis": kind["value_basis"],
        "temporal_context": kind["temporal_context"],
        "currency_field": currency,
        "unit": None,
        "nullable": fields[field].nullable,
        "is_unique": fields[field].unique,
        "sync_strategy": "batch",
    }
    return binding.model_dump(exclude={"evidence"}) | {
        "source_id": target.source_id,
        "field": field,
        "currency_field": currency,
        "mapping": mapping,
        "type": "binding",
        "status": "PROPOSED",
        "basis": "inferred",
        "evidence": _label_evidence(
            binding.evidence, _field_names(target), _foreign_key_names(target)
        ),
    }


def ground_items(
    proposal: OntologyProposal, metadata: list[DiscoveredObject], known_terms: list[str]
) -> tuple[list[dict[str, Any]], list[tuple[str, str]]]:
    """Each proposed item the input supports, and (item, reason) for each one refused.

    Items are judged one at a time, so one slip in a long answer does not discard the rest;
    nothing ungrounded is ever kept. A term or binding that leans on a refused term is refused
    with it.
    """
    all_fields = set().union(*(_field_names(item) for item in metadata))
    all_foreign_keys = set().union(*(_foreign_key_names(item) for item in metadata))

    refused: list[tuple[str, str]] = []
    candidates = list(proposal.terms)
    while True:
        allowed = set(known_terms) | {term.id for term in candidates}
        terms: list[dict[str, Any]] = []
        failed: set[str] = set()
        for term in candidates:
            try:
                terms.append(_ground_term(term, allowed, all_fields, all_foreign_keys))
            except UngroundedProposal as error:
                refused.append((term.id, str(error)))
                failed.add(term.id)
        if not failed:
            break
        # Judge the rest again without the refused terms, which some may have named.
        candidates = [term for term in candidates if term.id not in failed]

    allowed = set(known_terms) | {term["id"] for term in terms}
    grounded = terms
    for binding in proposal.bindings:
        try:
            grounded.append(_ground_binding(binding, metadata, allowed))
        except UngroundedProposal as error:
            refused.append((f"{binding.object}.{binding.field}", str(error)))
    return grounded, refused


def ground_proposal(
    proposal: OntologyProposal, metadata: list[DiscoveredObject], known_terms: list[str]
) -> list[dict[str, Any]]:
    """Every item grounded, or a refusal naming the first that is not."""
    grounded, refused = ground_items(proposal, metadata, known_terms)
    if refused:
        raise UngroundedProposal(refused[0][1])
    return grounded
