"""Resolved property views: a derived, per-run projection of observations (spec 5.5, 5.6).

A view is RESOLVED only when exactly one published authority assertion applies to the entity's
type and property at the run's time, and that source's observations agree. Otherwise it says
why not: AUTHORITY_CONFLICT for overlapping authority, STALE when the only authority has
expired, UNRESOLVED when there is none or it observed nothing, CONFLICT when the authoritative
observations disagree, and HUMAN_REVIEW while an identity review could still change the
entity. Every contributing observation is kept, authoritative or not, and no value is ever
chosen by ingestion order (AC-REC-12, AC-REC-16).
"""

import json
from collections import defaultdict
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, text

from app.domain.inputs import RunInputs

_OBSERVATIONS = text(
    """
    SELECT o.id, o.entity_id, e.class_iri, o.property_iri, o.source_id, o.source_record_id,
        o.normalized_value, o.numeric_value, o.currency, o.unit, o.value_basis,
        o.temporal_context, o.mapping_id, o.mapping_version, o.occurred_at, o.observed_at
    FROM property_observations o
    JOIN domain_entities e ON e.tenant_id = o.tenant_id AND e.id = o.entity_id
    WHERE o.mapping_id = ANY (CAST(:mappings AS uuid[]))
      AND o.source_record_id = ANY (CAST(:records AS uuid[]))
    ORDER BY o.entity_id, o.property_iri, o.id
    """
)


def pinned_observations(conn: Connection, inputs: RunInputs) -> list[Any]:
    """Only observations made under this run's mappings, from this run's records."""
    return conn.execute(
        _OBSERVATIONS,
        {
            "mappings": [m["id"] for m in inputs.mappings],
            "records": [str(r.id) for r in inputs.records],
        },
    ).all()


def _when(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _authority_state(
    authority: list[dict[str, Any]], class_iri: str, property_iri: str, as_of: datetime
) -> tuple[list[dict[str, Any]], bool]:
    """(assertions valid at `as_of`, whether any expired one exists) for one fact."""
    facts = [
        a for a in authority
        if a["property_iri"] == property_iri and a["entity_type_iri"] == class_iri
    ]
    valid = [
        a for a in facts
        if _when(a["valid_from"]) <= as_of
        and (a["valid_to"] is None or as_of < _when(a["valid_to"]))
    ]
    expired = any(a["valid_to"] is not None and _when(a["valid_to"]) <= as_of for a in facts)
    return valid, expired


def _resolve(
    observations: list[Any], valid: list[dict[str, Any]], expired: bool, in_review: bool
) -> dict[str, Any]:
    if in_review:
        return {"status": "HUMAN_REVIEW"}
    if len(valid) > 1:
        return {"status": "AUTHORITY_CONFLICT"}
    if not valid:
        return {"status": "STALE" if expired else "UNRESOLVED"}
    assertion = valid[0]
    held = [o for o in observations
            if str(o.source_id) == assertion["source_id"] and o.normalized_value is not None]
    values = {(o.normalized_value, o.currency) for o in held}
    if not values:
        return {"status": "UNRESOLVED", "authority": assertion}
    if len(values) > 1:
        return {"status": "CONFLICT", "authority": assertion}
    (value, currency), = values
    return {
        "status": "RESOLVED",
        "authority": assertion,
        "value": value,
        "numeric": held[0].numeric_value,
        "currency": currency,
    }


def _entities_in_review(conn: Connection) -> set[UUID]:
    rows = conn.execute(
        text(
            "SELECT unnest(candidate_entity_ids) FROM entity_resolution_decisions "
            "WHERE status = 'PENDING_REVIEW'"
        )
    ).scalars()
    return set(rows)


def resolve_views(
    conn: Connection, tenant_id: UUID, run_id: UUID, inputs: RunInputs, observations: list[Any]
) -> None:
    in_review = _entities_in_review(conn)
    grouped: dict[tuple[UUID, str, str], list[Any]] = defaultdict(list)
    for observation in observations:
        key = (observation.entity_id, observation.class_iri, observation.property_iri)
        grouped[key].append(observation)
    for (entity_id, class_iri, property_iri), group in sorted(grouped.items()):
        valid, expired = _authority_state(inputs.authority, class_iri, property_iri, inputs.as_of)
        view = _resolve(group, valid, expired, entity_id in in_review)
        authority = view.get("authority")
        conn.execute(
            text(
                """
                INSERT INTO resolved_property_views (id, tenant_id, run_id, entity_id,
                    property_iri, status, value, numeric_value, currency,
                    authority_assertion_id, authority_version, mapping_versions,
                    contributing_observation_ids, resolved_at)
                VALUES (:id, :tenant, :run, :entity, :property, :status, CAST(:value AS jsonb),
                    :numeric, :currency, :authority, :authority_version,
                    CAST(:mapping_versions AS jsonb), CAST(:observations AS uuid[]), :as_of)
                """
            ),
            {
                "id": uuid4(),
                "tenant": tenant_id,
                "run": run_id,
                "entity": entity_id,
                "property": property_iri,
                "status": view["status"],
                "value": json.dumps(view["value"]) if "value" in view else None,
                "numeric": view.get("numeric"),
                "currency": view.get("currency"),
                "authority": authority["id"] if authority else None,
                "authority_version": authority["version"] if authority else None,
                "mapping_versions": json.dumps(
                    {str(o.mapping_id): o.mapping_version for o in group}, sort_keys=True
                ),
                "observations": [str(o.id) for o in group],
                "as_of": inputs.as_of,
            },
        )
