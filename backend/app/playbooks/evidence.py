"""The evidence a playbook's preconditions see for one case (spec 9.1, 9.2, 8.2).

A snapshot holds the case, its entities and their direct neighbours, every observation of those
entities with the connector of the source that made it, the resolved views of the latest
successful run, and the relationships among them. It has the shape of a domain-engine snapshot
(app/domain/findings.py) plus `case` and `sources`, so pack code can index it the same way.

It is read in the caller's transaction, so request creation, execution-stage revalidation and
the Agent's dry-run tools all decide over current state, never over a cached copy.
"""

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, text


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return format(value, "f")
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _ids(values: list[Any]) -> list[str]:
    return sorted({str(v) for v in values})


def case_snapshot(conn: Connection, case_id: UUID) -> dict[str, Any] | None:
    case = conn.execute(
        text(
            """
            SELECT id, run_id, case_type, reconciliation_scope, status, entity_ids,
                property_iris, observation_ids, expected, observed, expected_amount,
                observed_amount, currency, evidence
            FROM domain_cases WHERE id = :id
            """
        ),
        {"id": case_id},
    ).one_or_none()
    if case is None:
        return None
    core = _ids(case.entity_ids)
    relationships = conn.execute(
        text(
            """
            SELECT id, subject_entity_id, predicate_iri, object_entity_id
            FROM domain_relationship_instances
            WHERE subject_entity_id = ANY (CAST(:ids AS uuid[]))
               OR object_entity_id = ANY (CAST(:ids AS uuid[]))
            ORDER BY id
            """
        ),
        {"ids": core},
    ).all()
    neighbours = {str(r.subject_entity_id) for r in relationships} | {
        str(r.object_entity_id) for r in relationships
    }
    entity_ids = sorted(set(core) | neighbours)
    entities = conn.execute(
        text("SELECT id, class_iri FROM domain_entities WHERE id = ANY (CAST(:ids AS uuid[]))"),
        {"ids": entity_ids},
    ).all()
    observations = conn.execute(
        text(
            """
            SELECT o.id, o.entity_id, o.property_iri, o.source_id, o.source_record_id,
                r.external_id, o.normalized_value, o.numeric_value, o.currency, o.unit,
                o.value_basis, o.temporal_context, o.occurred_at, o.observed_at
            FROM property_observations o
            JOIN source_records r ON r.tenant_id = o.tenant_id AND r.id = o.source_record_id
            WHERE o.entity_id = ANY (CAST(:ids AS uuid[]))
            ORDER BY o.entity_id, o.property_iri, o.observed_at, o.id
            """
        ),
        {"ids": entity_ids},
    ).all()
    source_ids = _ids([o.source_id for o in observations])
    sources = conn.execute(
        text("SELECT id, connector_id FROM data_sources WHERE id = ANY (CAST(:ids AS uuid[]))"),
        {"ids": source_ids},
    ).all()
    views = conn.execute(
        text(
            """
            SELECT v.entity_id, v.property_iri, v.status, v.value, v.numeric_value, v.currency,
                a.source_id AS authority_source_id, v.contributing_observation_ids
            FROM resolved_property_views v
            LEFT JOIN authority_assertions a ON a.id = v.authority_assertion_id
            WHERE v.entity_id = ANY (CAST(:ids AS uuid[]))
              AND v.run_id = (
                SELECT r.id FROM domain_runs r
                WHERE r.status = 'SUCCEEDED' AND EXISTS (
                    SELECT 1 FROM resolved_property_views x
                    WHERE x.run_id = r.id AND x.entity_id = ANY (CAST(:ids AS uuid[])))
                ORDER BY r.finished_at DESC, r.id LIMIT 1)
            ORDER BY v.entity_id, v.property_iri
            """
        ),
        {"ids": entity_ids},
    ).all()
    return {
        "case": {
            "id": str(case.id),
            "run_id": str(case.run_id),
            "case_type": case.case_type,
            "scope": case.reconciliation_scope,
            "status": case.status,
            "entity_ids": core,
            "property_iris": list(case.property_iris),
            "observation_ids": _ids(case.observation_ids),
            "expected": case.expected,
            "observed": case.observed,
            "expected_amount": _text(case.expected_amount),
            "observed_amount": _text(case.observed_amount),
            "currency": case.currency,
            "evidence": case.evidence,
        },
        "sources": {str(s.id): s.connector_id for s in sources},
        "entities": [{"id": str(e.id), "class_iri": e.class_iri}
                     for e in sorted(entities, key=lambda e: str(e.id))],
        "observations": [
            {
                "id": str(o.id),
                "entity_id": str(o.entity_id),
                "property_iri": o.property_iri,
                "source_id": str(o.source_id),
                "source_record_id": str(o.source_record_id),
                "external_id": o.external_id,
                "value": o.normalized_value,
                "numeric": _text(o.numeric_value),
                "currency": o.currency,
                "unit": o.unit,
                "value_basis": o.value_basis,
                "temporal_context": o.temporal_context,
                "occurred_at": _text(o.occurred_at),
                "observed_at": _text(o.observed_at),
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
                "authority_source_id": _text(v.authority_source_id),
                "observation_ids": _ids(v.contributing_observation_ids),
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
