"""The investigation tools (spec 8.2). Read, classify, compute and recommend; nothing else.

Every tool is bound to one investigated case. Entity and record arguments must lie inside that
case's evidence snapshot (the case's entities and their direct neighbours), so the Agent sees
only the subgraph the case needs (AC-AI-05) and only its own tenant's rows, under RLS
(AC-AI-01). No tool runs caller-supplied SQL text or reaches a provider (AC-AI-03): arguments
are typed values bound as parameters, and all data comes from SettleIQ's own tables.

Each tool returns its result and the IDs of the evidence it showed. The route records both in
the trace, and only those IDs may later appear in the agent's answer (AC-AI-04).
"""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection, text

from app.authz.access import EffectiveAccess
from app.domain_packs.manifest import TERM, AgentTool, load_domain_packs
from app.domain_packs.registry import resolve_entrypoint
from app.errors import ApiError, missing_permission, validation_failed
from app.investigation.response import CASE_TYPE, PLAYBOOK_ID
from app.playbooks.evidence import case_snapshot
from app.playbooks.policy import active_playbooks, approval_policy, evaluate, recommend
from app.sources.evidence import read_verified

RAW_EXCERPT_BYTES = 2000
MAX_RECORDS = 20
TERM_KEYS = ("@id", "@type", "rdfs:label", "rdfs:comment", "rdfs:subClassOf", "rdfs:domain",
             "rdfs:range")


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class NoArgs(_Args):
    pass


class AuthorityArgs(_Args):
    property_iri: str = Field(pattern=TERM)
    entity_type_iri: str = Field(pattern=TERM)


class RecordArgs(_Args):
    record_ids: list[UUID] = Field(min_length=1, max_length=MAX_RECORDS)


class EntityArgs(_Args):
    entity_id: UUID


class ObservationArgs(_Args):
    entity_id: UUID
    property_iri: str | None = Field(default=None, pattern=TERM)


class CaseEvidenceArgs(_Args):
    include_raw_excerpts: bool = False


class ClassifyArgs(_Args):
    classification: str = Field(pattern=CASE_TYPE)


class ValidateArgs(_Args):
    playbook: str = Field(pattern=PLAYBOOK_ID)


class PackToolArgs(_Args):
    entity_id: UUID | None = None


@dataclass
class Run:
    """One tool call's scope: the investigated case in its tenant, under POL-08 access."""

    conn: Connection
    tenant_id: UUID
    investigation_id: UUID
    case_id: UUID
    pack_namespace: str
    access: EffectiveAccess
    _snapshot: dict[str, Any] | None = field(default=None, repr=False)

    @property
    def snapshot(self) -> dict[str, Any]:
        if self._snapshot is None:
            self._snapshot = case_snapshot(self.conn, self.case_id)
            if self._snapshot is None:
                raise ApiError(404, "NOT_FOUND", "The investigated case is not visible.")
        return self._snapshot

    def entities(self) -> set[str]:
        return {e["id"] for e in self.snapshot["entities"]}

    def records(self) -> set[str]:
        ids = {o["source_record_id"] for o in self.snapshot["observations"]}
        return ids | set(self.snapshot["case"]["evidence"].get("source_record_ids", []))

    def require_entity(self, entity_id: UUID) -> str:
        if str(entity_id) not in self.entities():
            raise validation_failed("That entity is outside the investigated case.")
        return str(entity_id)


@dataclass(frozen=True)
class Output:
    result: dict[str, Any]
    evidence_ids: frozenset[str] = frozenset()


def _iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


def _row(row: Any) -> dict[str, Any]:
    out = {}
    for key, value in row._mapping.items():
        if isinstance(value, UUID):
            value = str(value)
        elif isinstance(value, list):
            value = [str(v) if isinstance(v, UUID) else v for v in value]
        else:
            value = _iso(value)
        out[key] = value if not hasattr(value, "as_tuple") else format(value, "f")
    return out


def _pinned_ontology(run: Run) -> Any:
    return run.conn.execute(
        text(
            """
            SELECT o.id, o.version, o.content_hash, o.composition, o.tenant_extension,
                o.draft_id
            FROM domain_cases c
            JOIN domain_runs r ON r.id = c.run_id
            JOIN ontologies o ON o.id = r.ontology_id
            WHERE c.id = :case
            """
        ),
        {"case": run.case_id},
    ).one()


def get_ontology_context(run: Run, args: NoArgs) -> Output:
    """The ontology terms this case uses, from the ontology version its run pinned."""
    ontology = _pinned_ontology(run)
    snapshot = run.snapshot
    wanted = {e["class_iri"] for e in snapshot["entities"]}
    wanted |= {o["property_iri"] for o in snapshot["observations"]}
    wanted |= {r["predicate"] for r in snapshot["relationships"]}
    rows = run.conn.execute(
        text("SELECT layer, namespace, content_hash, content FROM ontology_artifacts")
    ).all()
    contents = {(r.layer, r.namespace, r.content_hash): r.content for r in rows}
    graphs = [contents.get((ref["layer"], ref["namespace"], ref["content_hash"])) or {}
              for ref in ontology.composition]
    graphs.append(ontology.tenant_extension or {})
    terms = []
    for graph in graphs:
        for node in graph.get("@graph", []):
            if node.get("@id") in wanted:
                terms.append({key: node[key] for key in TERM_KEYS if key in node})
    return Output(
        {
            "ontology_id": str(ontology.id),
            "version": ontology.version,
            "content_hash": ontology.content_hash,
            "layers": [{"layer": r["layer"], "namespace": r["namespace"]}
                       for r in ontology.composition],
            "terms": sorted(terms, key=lambda t: t["@id"]),
        },
        frozenset({str(ontology.id)}),
    )


def get_source_authority(run: Run, args: AuthorityArgs) -> Output:
    """The authority for one exact fact and entity type, in the case's pinned ontology."""
    ontology = _pinned_ontology(run)
    rows = run.conn.execute(
        text(
            """
            SELECT a.id, a.property_iri, a.entity_type_iri, a.source_id, s.connector_id,
                a.version, a.valid_from, a.valid_to, a.verification_method
            FROM authority_assertions a JOIN data_sources s ON s.id = a.source_id
            WHERE a.draft_id = :draft AND a.status = 'ACTIVE'
              AND a.property_iri = :property AND a.entity_type_iri = :entity_type
            ORDER BY a.valid_from, a.id
            """
        ),
        {"draft": ontology.draft_id, "property": args.property_iri,
         "entity_type": args.entity_type_iri},
    ).all()
    assertions = [_row(r) for r in rows]
    ids = {a["id"] for a in assertions} | {a["source_id"] for a in assertions}
    return Output({"assertions": assertions}, frozenset(ids))


def get_source_records(run: Run, args: RecordArgs) -> Output:
    """Source records the case's evidence names, with their provenance."""
    requested = {str(r) for r in args.record_ids}
    outside = requested - run.records()
    if outside:
        raise validation_failed("Those records are outside the investigated case.")
    rows = run.conn.execute(
        text(
            """
            SELECT r.id, r.source_id, s.connector_id, r.external_id, r.record_type,
                r.attributes, r.raw_evidence_id, r.payload_hash, r.occurred_at, r.observed_at,
                e.retrieval_context->>'provenance' AS provenance
            FROM source_records r
            JOIN data_sources s ON s.id = r.source_id
            JOIN raw_evidence e ON e.id = r.raw_evidence_id
            WHERE r.id = ANY (CAST(:ids AS uuid[]))
            ORDER BY r.id
            """
        ),
        {"ids": sorted(requested)},
    ).all()
    records = [_row(r) for r in rows]
    ids = {r["id"] for r in records} | {r["source_id"] for r in records}
    return Output({"records": records}, frozenset(ids))


def trace_domain_relationships(run: Run, args: EntityArgs) -> Output:
    """Relationship instances touching one entity. Only registered relationships exist."""
    entity = run.require_entity(args.entity_id)
    classes = {e["id"]: e["class_iri"] for e in run.snapshot["entities"]}
    related = [
        {**r, "subject_class": classes.get(r["subject"]), "object_class": classes.get(r["object"])}
        for r in run.snapshot["relationships"] if entity in (r["subject"], r["object"])
    ]
    ids = {r["id"] for r in related} | {r["subject"] for r in related} | {
        r["object"] for r in related} | {entity}
    return Output({"entity_id": entity, "class_iri": classes.get(entity),
                   "relationships": related}, frozenset(ids))


def get_case_evidence(run: Run, args: CaseEvidenceArgs) -> Output:
    """The case's finding and evidence. Raw payload excerpts also need evidence.raw.read."""
    if args.include_raw_excerpts and not run.access.permits("evidence.raw.read"):
        raise missing_permission("evidence.raw.read")
    case = run.snapshot["case"]
    records = sorted(run.records())
    sources = sorted({o["source_id"] for o in run.snapshot["observations"]
                      if o["source_record_id"] in records})
    # One ready-made citation per case observation: the spec 8.3 evidence triple, so the
    # model never has to pair a record with its source itself.
    case_observations = set(case["observation_ids"])
    citations = sorted(
        ({"evidence_id": o["id"], "record_id": o["source_record_id"], "source_id": o["source_id"],
          "property_iri": o["property_iri"], "value": o["value"]}
         for o in run.snapshot["observations"] if o["id"] in case_observations),
        key=lambda c: c["evidence_id"],
    )
    result = {"case": case, "source_record_ids": records, "source_ids": sources,
              "citations": citations}
    ids = {case["id"], *case["observation_ids"], *records, *sources, *case["entity_ids"]}
    if args.include_raw_excerpts:
        rows = run.conn.execute(
            text(
                "SELECT id, raw_evidence_id FROM source_records "
                "WHERE id = ANY (CAST(:ids AS uuid[])) ORDER BY id"
            ),
            {"ids": records[:MAX_RECORDS]},
        ).all()
        excerpts = []
        for row in rows:
            data = read_verified(run.conn, row.raw_evidence_id)
            excerpts.append({
                "record_id": str(row.id),
                "raw_evidence_id": str(row.raw_evidence_id),
                "truncated": len(data) > RAW_EXCERPT_BYTES,
                "excerpt": data[:RAW_EXCERPT_BYTES].decode("utf-8", errors="replace"),
            })
            ids.add(str(row.raw_evidence_id))
        result["raw_excerpts"] = excerpts
    return Output(result, frozenset(ids))


def get_property_observations(run: Run, args: ObservationArgs) -> Output:
    """Every observation of one entity, with provenance and value and time metadata."""
    entity = run.require_entity(args.entity_id)
    sources = run.snapshot["sources"]
    observations = [
        {**o, "connector_id": sources.get(o["source_id"])}
        for o in run.snapshot["observations"]
        if o["entity_id"] == entity
        and (args.property_iri is None or o["property_iri"] == args.property_iri)
    ]
    views = [v for v in run.snapshot["views"] if v["entity_id"] == entity
             and (args.property_iri is None or v["property_iri"] == args.property_iri)]
    ids = {entity} | {o["id"] for o in observations} | {
        o["source_record_id"] for o in observations} | {o["source_id"] for o in observations}
    return Output({"entity_id": entity, "observations": observations, "resolved_views": views},
                  frozenset(ids))


def get_entity_resolution_trace(run: Run, args: EntityArgs) -> Output:
    """The identity decisions that selected or considered one entity."""
    entity = run.require_entity(args.entity_id)
    rows = run.conn.execute(
        text(
            """
            SELECT id, run_id, source_record_id, class_iri, policy_id, policy_version, method,
                features, candidate_entity_ids, selected_entity_id, status, reason
            FROM entity_resolution_decisions
            WHERE selected_entity_id = :e OR :e = ANY (candidate_entity_ids)
            ORDER BY id
            """
        ),
        {"e": entity},
    ).all()
    decisions = [_row(r) for r in rows]
    ids = {entity} | {d["id"] for d in decisions} | {d["source_record_id"] for d in decisions}
    return Output({"entity_id": entity, "decisions": decisions}, frozenset(ids))


def _case_types(run: Run) -> set[str]:
    pack = load_domain_packs().get(run.pack_namespace)
    return {c.code for c in pack.case_types} if pack else set()


def classify_domain_case(run: Run, args: ClassifyArgs) -> Output:
    """Check a proposed classification against the pack's taxonomy. Nothing is written but the
    trace: the case's own type and status never change (spec 8.1)."""
    if args.classification not in _case_types(run):
        raise validation_failed("That classification is not registered by the case's pack.")
    return Output({"classification": args.classification, "status": "PROPOSED",
                   "case_type": run.snapshot["case"]["case_type"]})


def _verdict(item: dict[str, Any]) -> dict[str, Any]:
    keep = ("playbook", "version", "eligible", "requires_approval", "failures", "target", "proof")
    return {key: item[key] for key in keep if key in item}


def recommend_playbook(run: Run, args: NoArgs) -> Output:
    """Eligible playbooks for the case, each with its approval requirement. A dry run."""
    items = recommend(run.conn, run.case_id) or []
    eligible = [_verdict(i) for i in items if i["eligible"]]
    ids = {e for i in items if i["eligible"] for e in i["evidence_ids"]}
    return Output({"eligible": eligible,
                   "ineligible_count": sum(1 for i in items if not i["eligible"])},
                  frozenset(ids))


def validate_playbook(run: Run, args: ValidateArgs) -> Output:
    """One playbook's verdict on the case, with its failures. A dry run with no side effect."""
    playbook = active_playbooks(run.conn).get(args.playbook)
    if playbook is None:
        raise validation_failed("That playbook is not active for this tenant.")
    result = evaluate(run.conn, run.case_id, playbook) or {}
    verdict = {
        "playbook": args.playbook,
        "version": playbook.definition.version,
        "requires_approval": approval_policy(run.conn, playbook).required,
        **{k: result.get(k) for k in ("eligible", "failures", "target", "proof")},
    }
    return Output(verdict, frozenset(result.get("evidence_ids", [])))


def _pack_tool(tool: AgentTool) -> Callable[[Run, PackToolArgs], Output]:
    """A pack tool: pack code over the case snapshot, called through the pack registry."""

    def call(run: Run, args: PackToolArgs) -> Output:
        if args.entity_id is not None:
            run.require_entity(args.entity_id)
        function = resolve_entrypoint(tool.entrypoint)
        raw = function(run.snapshot, args.model_dump(mode="json"))
        return Output(dict(raw["result"]), frozenset(str(i) for i in raw["evidence_ids"]))

    return call


CORE: dict[str, tuple[type[_Args], Callable[[Run, Any], Output]]] = {
    "get_ontology_context": (NoArgs, get_ontology_context),
    "get_source_authority": (AuthorityArgs, get_source_authority),
    "get_source_records": (RecordArgs, get_source_records),
    "trace_domain_relationships": (EntityArgs, trace_domain_relationships),
    "get_case_evidence": (CaseEvidenceArgs, get_case_evidence),
    "get_property_observations": (ObservationArgs, get_property_observations),
    "get_entity_resolution_trace": (EntityArgs, get_entity_resolution_trace),
    "classify_domain_case": (ClassifyArgs, classify_domain_case),
    "recommend_playbook": (NoArgs, recommend_playbook),
    "validate_playbook": (ValidateArgs, validate_playbook),
}


def implementation(tool: AgentTool) -> tuple[type[_Args], Callable[[Run, Any], Output]]:
    if tool.entrypoint is not None:
        return PackToolArgs, _pack_tool(tool)
    return CORE[tool.name]


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def redact(value: Any) -> Any:
    """A trace keeps raw payload excerpts only as a hash; the tool's output carried the text."""
    if isinstance(value, dict):
        return {k: ({"sha256": hashlib.sha256(str(v).encode()).hexdigest()}
                    if k == "excerpt" else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def _description(tool: AgentTool) -> str:
    if tool.entrypoint is not None:
        doc = resolve_entrypoint(tool.entrypoint).__doc__
    else:
        doc = CORE[tool.name][1].__doc__
    return " ".join((doc or tool.name).split())


def tool_specs(tools: list[AgentTool]) -> list[dict[str, Any]]:
    """What the agent is told about each tool. The dispatcher sends this with every run."""
    specs = []
    for tool in sorted(tools, key=lambda t: t.name):
        model, _ = implementation(tool)
        specs.append({"name": tool.name, "permission": tool.permission,
                      "description": _description(tool),
                      "parameters": model.model_json_schema()})
    return specs


def registry_version(tools: list[AgentTool]) -> str:
    """Pinned on each investigation, so a trace says which tool contract it ran under."""
    return digest(tool_specs(tools))
