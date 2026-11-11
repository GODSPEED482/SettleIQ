"""Domain projection, exact-ID identity resolution and observations (spec 5.5, 7.2, 7.6).

Each record a run can read is projected into the classes its mappings bind, plus any class the
policy derives from them. One `entity_resolution_decisions` row per record and class records
how it was matched, and only a decision creates an `entity_source_links` row (AC-REC-11).
Matching uses exact identifier values only (AC-REC-03). A record whose identifiers point at
two or more entities is left unlinked for review, never attached to one (AC-REC-06). A record
no mapping covers gets no decision at all and stays preserved as it was ingested.

A linked record writes one append-only observation per mapping that applies to the entity,
so two bindings to one property give two observations (AC-REC-10).
"""

import json
from collections import defaultdict
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, text

from app.domain.inputs import Record, RunInputs
from app.domain.values import normalize


def _applicable(inputs: RunInputs, record: Record) -> dict[str, list[dict[str, Any]]]:
    """For each class this record evidences, the mappings that describe that class."""
    mappings = inputs.mappings_for(record)
    identities = inputs.policy.identities
    by_class: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for mapping in mappings:
        by_class[mapping["class_iri"]].append(mapping)
    for source_class in list(by_class):
        for derived in inputs.policy.derived.get(source_class, []):
            if derived in by_class:
                continue
            keys = identities[derived]
            carried = [m for m in by_class[source_class] if m["property_iri"] in keys]
            if carried:
                by_class[derived] = carried
    return {cls: by_class[cls] for cls in sorted(by_class) if cls in identities}


def _identifiers(
    record: Record, mappings: list[dict[str, Any]], identity_properties: list[str]
) -> list[str]:
    keys = set()
    for mapping in mappings:
        if mapping["property_iri"] not in identity_properties:
            continue
        value = normalize(record.attributes, mapping)
        if value is not None and value.text:
            keys.add(f"{mapping['property_iri']}={value.text}")
    return sorted(keys)


def _candidates(conn: Connection, class_iri: str, keys: list[str]) -> list[UUID]:
    rows = conn.execute(
        text(
            """
            SELECT DISTINCT e.id FROM domain_entities e
            WHERE e.class_iri = :class AND (
                e.canonical_key = ANY (:keys) OR EXISTS (
                    SELECT 1 FROM property_observations o
                    WHERE o.tenant_id = e.tenant_id AND o.entity_id = e.id
                      AND o.property_iri || '=' || o.normalized_value = ANY (:keys)))
            ORDER BY e.id
            """
        ),
        {"class": class_iri, "keys": keys},
    ).scalars()
    return list(rows)


def _decide(
    conn: Connection,
    tenant_id: UUID,
    run_id: UUID,
    record: Record,
    class_iri: str,
    policy_id: str,
    policy_version: str,
    *,
    status: str,
    keys: list[str],
    candidates: list[UUID],
    selected: UUID | None,
) -> UUID:
    decision_id = uuid4()
    conn.execute(
        text(
            """
            INSERT INTO entity_resolution_decisions (id, tenant_id, run_id, source_record_id,
                class_iri, policy_id, policy_version, method, features, candidate_entity_ids,
                selected_entity_id, status)
            VALUES (:id, :tenant, :run, :record, :class, :policy, :version, 'EXACT_ID',
                CAST(:features AS jsonb), CAST(:candidates AS uuid[]), :selected, :status)
            """
        ),
        {
            "id": decision_id,
            "tenant": tenant_id,
            "run": run_id,
            "record": record.id,
            "class": class_iri,
            "policy": policy_id,
            "version": policy_version,
            "features": json.dumps({"identifiers": keys}),
            "candidates": [str(c) for c in candidates],
            "selected": selected,
            "status": status,
        },
    )
    return decision_id


def link(conn: Connection, tenant_id: UUID, entity_id: UUID, record_id: UUID,
         decision_id: UUID) -> None:
    conn.execute(
        text(
            """
            INSERT INTO entity_source_links (id, tenant_id, entity_id, source_record_id,
                decision_id)
            VALUES (:id, :tenant, :entity, :record, :decision)
            ON CONFLICT (tenant_id, entity_id, source_record_id) DO NOTHING
            """
        ),
        {"id": uuid4(), "tenant": tenant_id, "entity": entity_id, "record": record_id,
         "decision": decision_id},
    )


def observe(
    conn: Connection, tenant_id: UUID, record: Record, entity_id: UUID,
    mappings: list[dict[str, Any]],
) -> None:
    for mapping in mappings:
        value = normalize(record.attributes, mapping)
        if value is None:
            continue
        conn.execute(
            text(
                """
                INSERT INTO property_observations (id, tenant_id, entity_id, property_iri,
                    source_record_id, source_id, raw_evidence_id, evidence_hash, field_path,
                    mapping_id, mapping_version, raw_value, normalized_value, numeric_value,
                    datatype, unit, currency, value_basis, temporal_context, transform,
                    occurred_at, observed_at)
                VALUES (:id, :tenant, :entity, :property, :record, :source, :evidence, :hash,
                    :field_path, :mapping, :mapping_version, CAST(:raw AS jsonb), :normalized,
                    :numeric, :datatype, :unit, :currency, :value_basis, :temporal_context,
                    CAST(:transform AS jsonb), :occurred_at, :observed_at)
                ON CONFLICT (tenant_id, source_record_id, mapping_id, entity_id) DO NOTHING
                """
            ),
            {
                "id": uuid4(),
                "tenant": tenant_id,
                "entity": entity_id,
                "property": mapping["property_iri"],
                "record": record.id,
                "source": record.source_id,
                "evidence": record.raw_evidence_id,
                "hash": record.payload_hash,
                "field_path": mapping["field_path"],
                "mapping": mapping["id"],
                "mapping_version": mapping["version"],
                "raw": json.dumps(value.raw, default=str),
                "normalized": value.text,
                "numeric": value.numeric,
                "datatype": mapping["datatype"],
                "unit": mapping["unit"],
                "currency": value.currency,
                "value_basis": mapping["value_basis"],
                "temporal_context": mapping["temporal_context"],
                "transform": json.dumps(mapping["transform"]),
                "occurred_at": record.occurred_at,
                "observed_at": record.observed_at,
            },
        )


def _new_entity(conn: Connection, tenant_id: UUID, run_id: UUID, inputs: RunInputs,
                class_iri: str, key: str) -> UUID:
    entity_id = uuid4()
    conn.execute(
        text(
            """
            INSERT INTO domain_entities (id, tenant_id, ontology_id, class_iri, canonical_key,
                created_run_id)
            VALUES (:id, :tenant, :ontology, :class, :key, :run)
            """
        ),
        {"id": entity_id, "tenant": tenant_id, "ontology": inputs.ontology_id,
         "class": class_iri, "key": key, "run": run_id},
    )
    return entity_id


def project(conn: Connection, tenant_id: UUID, run_id: UUID, inputs: RunInputs) -> None:
    """Decide every undecided (record, class) pair in ingestion order, then observe all links."""
    decided = {
        (row.source_record_id, row.class_iri)
        for row in conn.execute(
            text("SELECT source_record_id, class_iri FROM entity_resolution_decisions")
        )
    }
    policy_id, policy_version = next(iter(inputs.policy.versions.items()), ("none", "0"))
    for record in inputs.records:
        for class_iri, mappings in _applicable(inputs, record).items():
            if (record.id, class_iri) in decided:
                continue
            keys = _identifiers(record, mappings, inputs.policy.identities[class_iri])
            common = {"keys": keys}
            if not keys:
                _decide(conn, tenant_id, run_id, record, class_iri, policy_id, policy_version,
                        status="UNMATCHED", candidates=[], selected=None, **common)
                continue
            candidates = _candidates(conn, class_iri, keys)
            if len(candidates) > 1:
                _decide(conn, tenant_id, run_id, record, class_iri, policy_id, policy_version,
                        status="PENDING_REVIEW", candidates=candidates, selected=None, **common)
                continue
            if candidates:
                entity_id, status = candidates[0], "AUTO_RESOLVED"
            else:
                entity_id = _new_entity(conn, tenant_id, run_id, inputs, class_iri, keys[0])
                status = "NEW_ENTITY"
            decision_id = _decide(
                conn, tenant_id, run_id, record, class_iri, policy_id, policy_version,
                status=status, candidates=candidates, selected=entity_id, **common,
            )
            link(conn, tenant_id, entity_id, record.id, decision_id)
            # Observed now, so the next record in this run can match on these identifiers.
            observe(conn, tenant_id, record, entity_id, mappings)
    observe_links(conn, tenant_id, inputs)


def observe_links(conn: Connection, tenant_id: UUID, inputs: RunInputs) -> None:
    """Observe every link under the pinned mappings, including links accepted in review."""
    records = {record.id: record for record in inputs.records}
    rows = conn.execute(
        text(
            """
            SELECT l.source_record_id, l.entity_id, e.class_iri
            FROM entity_source_links l
            JOIN domain_entities e ON e.tenant_id = l.tenant_id AND e.id = l.entity_id
            ORDER BY l.source_record_id, l.entity_id
            """
        )
    ).all()
    for row in rows:
        record = records.get(row.source_record_id)
        if record is None:
            continue
        mappings = _applicable(inputs, record).get(row.class_iri)
        if mappings:
            observe(conn, tenant_id, record, row.entity_id, mappings)


def relate(conn: Connection, tenant_id: UUID, run_id: UUID, inputs: RunInputs) -> None:
    """Instantiate policy relationships where exactly one object shares the subject's value."""
    for rule in inputs.policy.relationships:
        rows = conn.execute(
            text(
                """
                SELECT s.id AS subject, o.id AS object, so.normalized_value AS value,
                    array_agg(DISTINCT so.id) || array_agg(DISTINCT oo.id) AS observations
                FROM domain_entities s
                JOIN property_observations so ON so.tenant_id = s.tenant_id
                    AND so.entity_id = s.id AND so.property_iri = :via
                    AND so.normalized_value IS NOT NULL
                JOIN property_observations oo ON oo.tenant_id = so.tenant_id
                    AND oo.property_iri = :via AND oo.normalized_value = so.normalized_value
                JOIN domain_entities o ON o.tenant_id = oo.tenant_id AND o.id = oo.entity_id
                    AND o.class_iri = :object
                WHERE s.class_iri = :subject AND s.id <> o.id
                GROUP BY s.id, o.id, so.normalized_value
                ORDER BY s.id, o.id
                """
            ),
            {"via": rule["via"], "subject": rule["subject"], "object": rule["object"]},
        ).all()
        by_subject: dict[UUID, list[Any]] = defaultdict(list)
        for row in rows:
            by_subject[row.subject].append(row)
        for subject, matches in by_subject.items():
            if len({m.object for m in matches}) != 1:
                continue  # ambiguous: never attach a subject to one of several objects
            match = matches[0]
            conn.execute(
                text(
                    """
                    INSERT INTO domain_relationship_instances (id, tenant_id, ontology_id,
                        subject_entity_id, predicate_iri, object_entity_id, provenance,
                        created_run_id)
                    VALUES (:id, :tenant, :ontology, :subject, :predicate, :object,
                        CAST(:provenance AS jsonb), :run)
                    ON CONFLICT (tenant_id, subject_entity_id, predicate_iri, object_entity_id)
                        DO NOTHING
                    """
                ),
                {
                    "id": uuid4(),
                    "tenant": tenant_id,
                    "ontology": inputs.ontology_id,
                    "subject": subject,
                    "predicate": rule["predicate"],
                    "object": match.object,
                    "provenance": json.dumps({
                        "rule": rule,
                        "value": match.value,
                        "observation_ids": sorted(str(o) for o in match.observations),
                    }),
                    "run": run_id,
                },
            )
