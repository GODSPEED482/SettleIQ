"""One row in every domain table for a tenant, written directly as the runtime role (task 3.7).

The isolation tests need real rows to fail to see, update or reference. Each row is the
smallest the constraints allow; the domain pipeline tests build realistic data instead.
"""

import hashlib
import json
from uuid import UUID, uuid4

from sqlalchemy import text

from app.db import scoped_tx

HASH = hashlib.sha256(b"domain-row").hexdigest()


def seed_domain_rows(tenant: UUID, user: UUID) -> dict[str, UUID]:
    ids = {name: uuid4() for name in (
        "draft", "ontology", "source", "object", "evidence", "record", "mapping",
        "domain_runs", "domain_entities", "other_entity", "entity_resolution_decisions",
        "entity_source_links", "property_observations", "resolved_property_views",
        "domain_relationship_instances", "domain_cases",
    )}
    p = {**ids, "tenant": tenant, "user": user, "hash": HASH}
    statements = [
        """INSERT INTO ontology_drafts (id, tenant_id, base_version, composition, tenant_extension,
               created_by)
           VALUES (:draft, :tenant, 0, '[]', '[]', :user)""",
        """INSERT INTO ontologies (id, tenant_id, version, composition, tenant_extension,
               content_hash, draft_id, published_by)
           VALUES (:ontology, :tenant, 1, '[]', '[]', :hash, :draft, :user)""",
        """INSERT INTO data_sources (id, tenant_id, connector_id, connector_version, source_type,
               protocol, display_name, created_by)
           VALUES (:source, :tenant, 'paypal', '1.0.0', 'payment_provider', 'http', 'P', :user)""",
        """INSERT INTO source_objects (id, tenant_id, source_id, object_name, object_type)
           VALUES (:object, :tenant, :source, 'captures', 'API')""",
        """INSERT INTO raw_evidence (id, tenant_id, source_id, evidence_type, storage_key,
               content_hash, byte_size, content_type)
           VALUES (:evidence, :tenant, :source, 'RECORD', 'k', :hash, 1, 'application/json')""",
        """INSERT INTO source_records (id, tenant_id, source_id, source_object_id, external_id,
               record_type, attributes, raw_evidence_id, observed_at, payload_hash,
               connector_version)
           VALUES (:record, :tenant, :source, :object, 'CAP-1', 'paypal:capture', '{}',
               :evidence, now(), :hash, '1.0.0')""",
        """INSERT INTO source_mappings (id, tenant_id, draft_id, source_id, external_object,
               field_path, property_iri, class_iri, version, status, datatype, transform,
               nullable, is_unique, sync_strategy, value_basis, temporal_context, method,
               created_by)
           VALUES (:mapping, :tenant, :draft, :source, 'captures', '$.id',
               'finops:providerReference', 'finops:Capture', 1, 'ACTIVE', 'string',
               '{"name": "identity"}', false, true, 'event', 'reference', 'at_capture',
               'human', :user)""",
        """INSERT INTO domain_runs (id, tenant_id, pack_namespace, pack_version, ontology_id,
               ontology_version, ontology_content_hash, mapping_versions, authority_versions,
               policy_versions, rule_version, source_snapshots, inputs_hash, status,
               requested_by)
           VALUES (:domain_runs, :tenant, 'finops', '0.2.0', :ontology, 1, :hash, '{}', '{}',
               '{}', '1', '{}', :hash, 'RUNNING', :user)""",
        """INSERT INTO domain_entities (id, tenant_id, ontology_id, class_iri, canonical_key,
               created_run_id)
           VALUES (:domain_entities, :tenant, :ontology, 'finops:Capture',
               'finops:providerReference=CAP-1', :domain_runs),
                  (:other_entity, :tenant, :ontology, 'finops:Order',
               'finops:orderReference=ORD-1', :domain_runs)""",
        """INSERT INTO entity_resolution_decisions (id, tenant_id, run_id, source_record_id,
               class_iri, policy_id, policy_version, method, features, selected_entity_id,
               status)
           VALUES (:entity_resolution_decisions, :tenant, :domain_runs, :record,
               'finops:Capture', 'finops.exact_id', '1', 'EXACT_ID', '{}', :domain_entities,
               'NEW_ENTITY')""",
        """INSERT INTO entity_source_links (id, tenant_id, entity_id, source_record_id,
               decision_id)
           VALUES (:entity_source_links, :tenant, :domain_entities, :record,
               :entity_resolution_decisions)""",
        """INSERT INTO property_observations (id, tenant_id, entity_id, property_iri,
               source_record_id, source_id, raw_evidence_id, evidence_hash, field_path,
               mapping_id, mapping_version, raw_value, normalized_value, datatype, value_basis,
               temporal_context, transform, observed_at)
           VALUES (:property_observations, :tenant, :domain_entities,
               'finops:providerReference', :record, :source, :evidence, :hash, '$.id',
               :mapping, 1, '"CAP-1"', 'CAP-1', 'string', 'reference', 'at_capture',
               '{"name": "identity"}', now())""",
        """INSERT INTO resolved_property_views (id, tenant_id, run_id, entity_id, property_iri,
               status, mapping_versions, contributing_observation_ids)
           VALUES (:resolved_property_views, :tenant, :domain_runs, :domain_entities,
               'finops:providerReference', 'UNRESOLVED', '{}',
               ARRAY[CAST(:property_observations AS uuid)])""",
        """INSERT INTO domain_relationship_instances (id, tenant_id, ontology_id,
               subject_entity_id, predicate_iri, object_entity_id, provenance, created_run_id)
           VALUES (:domain_relationship_instances, :tenant, :ontology, :domain_entities,
               'finops:forOrder', :other_entity, '{}', :domain_runs)""",
        """INSERT INTO domain_cases (id, tenant_id, run_id, case_type, reconciliation_scope,
               severity, fingerprint, entity_ids, expected_amount, observed_amount, currency,
               evidence, method, rule_version, version_refs)
           VALUES (:domain_cases, :tenant, :domain_runs, 'SETTLEMENT_VARIANCE', 'INTER_ENTITY',
               'high', :hash, ARRAY[CAST(:domain_entities AS uuid)], 100.000001, 99.5, 'USD',
               :evidence_json, 'test', '1', '{}')""",
    ]
    p["evidence_json"] = json.dumps({"seeded": True})
    with scoped_tx(tenant) as conn:
        for statement in statements:
            conn.execute(text(statement), p)
    return ids


DOMAIN_TABLES = (
    "domain_runs",
    "domain_entities",
    "entity_resolution_decisions",
    "entity_source_links",
    "property_observations",
    "resolved_property_views",
    "domain_relationship_instances",
    "domain_cases",
)
