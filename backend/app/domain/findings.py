"""The contract between a run and its pack's domain engine, and the cases it produces.

The engine receives a plain, sorted snapshot (no database, no core types) and returns finding
dicts. Core validates every finding, adds the evidence AC-REC-08 requires (affected records
and resolution state), fingerprints it, and opens a case unless an open case already holds
the same finding. The fingerprint covers only the finding, so identical version-pinned inputs
give identical fingerprints across runs.
"""

import json
from collections import defaultdict
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection, text

from app.domain.inputs import RunInputs
from app.semantics.hashing import content_hash

AMOUNT = r"^-?\d{1,14}(\.\d{1,6})?$"


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_type: str
    scope: Literal["INTRA_ENTITY", "INTER_ENTITY"]
    entity_ids: list[UUID] = Field(min_length=1)
    property_iris: list[str] = []
    observation_ids: list[UUID] = []
    relationship_ids: list[UUID] = []
    expected: Any = None
    observed: Any = None
    expected_amount: str | None = Field(default=None, pattern=AMOUNT)
    observed_amount: str | None = Field(default=None, pattern=AMOUNT)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    method: str = Field(min_length=1)
    evidence: dict[str, Any] = {}

    def fingerprint(self) -> str:
        return content_hash(self.model_dump(mode="json"))


class EngineError(RuntimeError):
    pass


def _text(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return format(value, "f")
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def snapshot(conn: Connection, run_id: UUID, inputs: RunInputs,
             observations: list[Any]) -> dict[str, Any]:
    views = conn.execute(
        text(
            """
            SELECT entity_id, property_iri, status, value, numeric_value, currency,
                authority_assertion_id, contributing_observation_ids
            FROM resolved_property_views WHERE run_id = :run
            ORDER BY entity_id, property_iri
            """
        ),
        {"run": run_id},
    ).all()
    entity_ids = sorted({o.entity_id for o in observations})
    relationships = conn.execute(
        text(
            """
            SELECT id, subject_entity_id, predicate_iri, object_entity_id
            FROM domain_relationship_instances
            WHERE subject_entity_id = ANY (CAST(:ids AS uuid[]))
              AND object_entity_id = ANY (CAST(:ids AS uuid[]))
            ORDER BY id
            """
        ),
        {"ids": [str(e) for e in entity_ids]},
    ).all()
    classes = {o.entity_id: o.class_iri for o in observations}
    authority_sources = {a["id"]: a["source_id"] for a in inputs.authority}
    return {
        "as_of": inputs.as_of.isoformat(),
        "entities": [{"id": str(e), "class_iri": classes[e]} for e in entity_ids],
        "observations": [
            {
                "id": str(o.id),
                "entity_id": str(o.entity_id),
                "property_iri": o.property_iri,
                "source_id": str(o.source_id),
                "value": o.normalized_value,
                "numeric": _text(o.numeric_value),
                "currency": o.currency,
                "unit": o.unit,
                "value_basis": o.value_basis,
                "temporal_context": o.temporal_context,
                "occurred_at": _text(o.occurred_at),
            }
            for o in observations
        ],
        "views": [
            {
                "entity_id": str(v.entity_id),
                "property_iri": v.property_iri,
                "status": v.status,
                "value": v.value,
                "numeric": _text(v.numeric_value),
                "currency": v.currency,
                "authority_source_id": authority_sources.get(_text(v.authority_assertion_id)),
                "observation_ids": sorted(str(i) for i in v.contributing_observation_ids),
            }
            for v in views
        ],
        "relationships": [
            {
                "id": str(r.id),
                "subject": str(r.subject_entity_id),
                "predicate": r.predicate_iri,
                "object": str(r.object_entity_id),
            }
            for r in relationships
        ],
    }


def validate(raw: Any, inputs: RunInputs, state: dict[str, Any]) -> list[Finding]:
    """Refuse anything the engine could not have derived from this snapshot."""
    if not isinstance(raw, list):
        raise EngineError("the domain engine must return a list of findings")
    case_types = {case.code for case in inputs.pack.case_types}
    entities = {e["id"] for e in state["entities"]}
    observations = {o["id"] for o in state["observations"]}
    relationships = {r["id"] for r in state["relationships"]}
    findings = []
    for item in raw:
        finding = Finding.model_validate(item)
        if finding.case_type not in case_types:
            raise EngineError(f"undeclared case type {finding.case_type}")
        if not {str(i) for i in finding.entity_ids} <= entities:
            raise EngineError("a finding names an entity outside the run")
        if not {str(i) for i in finding.observation_ids} <= observations:
            raise EngineError("a finding names an observation outside the run")
        if not {str(i) for i in finding.relationship_ids} <= relationships:
            raise EngineError("a finding names a relationship outside the run")
        if (finding.expected_amount or finding.observed_amount) and not finding.currency:
            raise EngineError("an amount needs a currency")
        findings.append(finding)
    return sorted(findings, key=Finding.fingerprint)


def _record_evidence(state: dict[str, Any], finding: Finding,
                     records: dict[str, str]) -> dict[str, Any]:
    wanted = {str(i) for i in finding.observation_ids}
    entities = {str(i) for i in finding.entity_ids}
    resolution = defaultdict(dict)
    for view in state["views"]:
        if view["entity_id"] in entities and (
            not finding.property_iris or view["property_iri"] in finding.property_iris
        ):
            resolution[view["entity_id"]][view["property_iri"]] = view["status"]
    return {
        "finding": finding.evidence,
        "source_record_ids": sorted({records[o] for o in wanted if o in records}),
        "resolution": dict(sorted(resolution.items())),
    }


def open_cases(
    conn: Connection, tenant_id: UUID, run_id: UUID, inputs: RunInputs, pins: dict[str, Any],
    state: dict[str, Any], findings: list[Finding], observations: list[Any],
) -> list[dict[str, Any]]:
    severity = {case.code: case.default_severity for case in inputs.pack.case_types}
    records = {str(o.id): str(o.source_record_id) for o in observations}
    version_refs = {
        "run_id": str(run_id),
        "ontology_version": pins["ontology_version"],
        "ontology_content_hash": pins["ontology_content_hash"],
        "pack_version": pins["pack_version"],
        "rule_version": pins["rule_version"],
        "policy_versions": pins["policy_versions"],
        "inputs_hash": content_hash(pins),
    }
    summary = []
    for finding in findings:
        fingerprint = finding.fingerprint()
        existing = conn.execute(
            text(
                "SELECT id FROM domain_cases WHERE fingerprint = :f "
                "AND status NOT IN ('DISMISSED', 'CLOSED')"
            ),
            {"f": fingerprint},
        ).scalar()
        case_id = existing or uuid4()
        if existing is None:
            conn.execute(
                text(
                    """
                    INSERT INTO domain_cases (id, tenant_id, run_id, case_type,
                        reconciliation_scope, severity, fingerprint, entity_ids, property_iris,
                        relationship_ids, observation_ids, expected, observed, expected_amount,
                        observed_amount, currency, evidence, method, rule_version, version_refs)
                    VALUES (:id, :tenant, :run, :case_type, :scope, :severity, :fingerprint,
                        CAST(:entities AS uuid[]), :properties, CAST(:relationships AS uuid[]),
                        CAST(:observations AS uuid[]), CAST(:expected AS jsonb),
                        CAST(:observed AS jsonb), :expected_amount, :observed_amount,
                        :currency, CAST(:evidence AS jsonb), :method, :rule_version,
                        CAST(:version_refs AS jsonb))
                    """
                ),
                {
                    "id": case_id,
                    "tenant": tenant_id,
                    "run": run_id,
                    "case_type": finding.case_type,
                    "scope": finding.scope,
                    "severity": severity[finding.case_type],
                    "fingerprint": fingerprint,
                    "entities": [str(i) for i in finding.entity_ids],
                    "properties": finding.property_iris,
                    "relationships": [str(i) for i in finding.relationship_ids],
                    "observations": [str(i) for i in finding.observation_ids],
                    "expected": json.dumps(finding.expected),
                    "observed": json.dumps(finding.observed),
                    "expected_amount": finding.expected_amount,
                    "observed_amount": finding.observed_amount,
                    "currency": finding.currency,
                    "evidence": json.dumps(_record_evidence(state, finding, records)),
                    "method": finding.method,
                    "rule_version": pins["rule_version"],
                    "version_refs": json.dumps(version_refs),
                },
            )
        summary.append({
            "fingerprint": fingerprint,
            "case_type": finding.case_type,
            "scope": finding.scope,
            "case_id": str(case_id),
            "new_case": existing is None,
        })
    return summary
