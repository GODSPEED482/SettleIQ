"""Canonical domain tables: entities, identity decisions, observations, runs and cases (spec 6.3).

Every table is tenant-owned, RLS-protected, and references its neighbours through
(tenant_id, id) composite foreign keys, so no row can point into another tenant even though
foreign-key checks bypass row-level security. Money is NUMERIC(20,6) with an explicit currency.

Observations, entities, links, resolved views and relationship instances are append-only for
the application (SELECT, INSERT). The rows that do change are guarded by triggers: a run
changes only while RUNNING, an identity decision only once out of PENDING_REVIEW, and a case
only along the spec 9.3 transitions.

Revision ID: 0011
Revises: 0010
"""

from alembic import op

from app.migration_support import APP_ROLE, enable_tenant_rls

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

TENANT_TABLES = (
    "domain_runs",
    "domain_entities",
    "entity_resolution_decisions",
    "entity_source_links",
    "property_observations",
    "resolved_property_views",
    "domain_relationship_instances",
    "domain_cases",
)
APPEND_ONLY = (
    "domain_entities",
    "entity_source_links",
    "property_observations",
    "resolved_property_views",
    "domain_relationship_instances",
)
CURRENCY = "~ '^[A-Z]{3}$'"
CASE_STATUSES = (
    "'OPEN', 'INVESTIGATING', 'HUMAN_REVIEW', 'ACTION_PENDING', 'RESOLVED', 'DISMISSED', 'CLOSED'"
)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE domain_runs (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants (id),
            pack_namespace text NOT NULL,
            pack_version text NOT NULL,
            ontology_id uuid NOT NULL,
            ontology_version integer NOT NULL,
            ontology_content_hash text NOT NULL CHECK (ontology_content_hash ~ '^[0-9a-f]{64}$'),
            mapping_versions jsonb NOT NULL,
            authority_versions jsonb NOT NULL,
            policy_versions jsonb NOT NULL,
            rule_version text NOT NULL,
            source_snapshots jsonb NOT NULL,
            inputs_hash text NOT NULL CHECK (inputs_hash ~ '^[0-9a-f]{64}$'),
            status text NOT NULL CHECK (status IN ('RUNNING', 'SUCCEEDED', 'FAILED')),
            findings jsonb NOT NULL DEFAULT '[]',
            failure text,
            requested_by uuid NOT NULL REFERENCES users (id),
            started_at timestamptz NOT NULL DEFAULT now(),
            finished_at timestamptz,
            UNIQUE (tenant_id, id),
            FOREIGN KEY (tenant_id, ontology_id) REFERENCES ontologies (tenant_id, id)
        )
        """
    )
    op.execute("CREATE INDEX domain_runs_tenant_idx ON domain_runs (tenant_id, started_at)")
    op.execute(
        """
        CREATE TABLE domain_entities (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants (id),
            ontology_id uuid NOT NULL,
            class_iri text NOT NULL,
            canonical_key text NOT NULL,
            created_run_id uuid NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, class_iri, canonical_key),
            FOREIGN KEY (tenant_id, ontology_id) REFERENCES ontologies (tenant_id, id),
            FOREIGN KEY (tenant_id, created_run_id) REFERENCES domain_runs (tenant_id, id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE entity_resolution_decisions (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants (id),
            run_id uuid NOT NULL,
            source_record_id uuid NOT NULL,
            class_iri text NOT NULL,
            policy_id text NOT NULL,
            policy_version text NOT NULL,
            method text NOT NULL CHECK (method IN ('EXACT_ID')),
            features jsonb NOT NULL,
            candidate_entity_ids uuid[] NOT NULL DEFAULT '{}',
            selected_entity_id uuid,
            status text NOT NULL CHECK (status IN ('NEW_ENTITY', 'AUTO_RESOLVED',
                'PENDING_REVIEW', 'ACCEPTED', 'REJECTED', 'UNMATCHED')),
            reviewer_id uuid REFERENCES users (id),
            reviewed_at timestamptz,
            reason text,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, source_record_id, class_iri),
            CHECK ((status IN ('NEW_ENTITY', 'AUTO_RESOLVED', 'ACCEPTED'))
                = (selected_entity_id IS NOT NULL)),
            CHECK ((status IN ('ACCEPTED', 'REJECTED'))
                = (reviewer_id IS NOT NULL AND reviewed_at IS NOT NULL AND reason IS NOT NULL)),
            FOREIGN KEY (tenant_id, run_id) REFERENCES domain_runs (tenant_id, id),
            FOREIGN KEY (tenant_id, source_record_id) REFERENCES source_records (tenant_id, id),
            FOREIGN KEY (tenant_id, selected_entity_id) REFERENCES domain_entities (tenant_id, id)
        )
        """
    )
    op.execute(
        "CREATE INDEX entity_resolution_status_idx "
        "ON entity_resolution_decisions (tenant_id, status)"
    )
    op.execute(
        """
        CREATE TABLE entity_source_links (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants (id),
            entity_id uuid NOT NULL,
            source_record_id uuid NOT NULL,
            decision_id uuid NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, entity_id, source_record_id),
            FOREIGN KEY (tenant_id, entity_id) REFERENCES domain_entities (tenant_id, id),
            FOREIGN KEY (tenant_id, source_record_id) REFERENCES source_records (tenant_id, id),
            FOREIGN KEY (tenant_id, decision_id)
                REFERENCES entity_resolution_decisions (tenant_id, id)
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE property_observations (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants (id),
            entity_id uuid NOT NULL,
            property_iri text NOT NULL,
            source_record_id uuid NOT NULL,
            source_id uuid NOT NULL,
            raw_evidence_id uuid NOT NULL,
            evidence_hash text NOT NULL CHECK (evidence_hash ~ '^[0-9a-f]{{64}}$'),
            field_path text NOT NULL,
            mapping_id uuid NOT NULL,
            mapping_version integer NOT NULL,
            raw_value jsonb NOT NULL,
            normalized_value text,
            numeric_value numeric(20, 6),
            datatype text NOT NULL,
            unit text,
            currency text CHECK (currency {CURRENCY}),
            value_basis text NOT NULL,
            temporal_context text NOT NULL,
            transform jsonb NOT NULL,
            occurred_at timestamptz,
            observed_at timestamptz NOT NULL,
            ingested_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, source_record_id, mapping_id, entity_id),
            FOREIGN KEY (tenant_id, entity_id) REFERENCES domain_entities (tenant_id, id),
            FOREIGN KEY (tenant_id, source_record_id) REFERENCES source_records (tenant_id, id),
            FOREIGN KEY (tenant_id, source_id) REFERENCES data_sources (tenant_id, id),
            FOREIGN KEY (tenant_id, raw_evidence_id) REFERENCES raw_evidence (tenant_id, id),
            FOREIGN KEY (tenant_id, mapping_id) REFERENCES source_mappings (tenant_id, id)
        )
        """
    )
    op.execute(
        "CREATE INDEX property_observations_entity_idx "
        "ON property_observations (tenant_id, entity_id, property_iri)"
    )
    op.execute(
        "CREATE INDEX property_observations_value_idx "
        "ON property_observations (tenant_id, property_iri, normalized_value)"
    )
    op.execute(
        f"""
        CREATE TABLE resolved_property_views (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants (id),
            run_id uuid NOT NULL,
            entity_id uuid NOT NULL,
            property_iri text NOT NULL,
            status text NOT NULL CHECK (status IN ('RESOLVED', 'CONFLICT', 'UNRESOLVED', 'STALE',
                'HUMAN_REVIEW', 'AUTHORITY_CONFLICT')),
            value jsonb,
            numeric_value numeric(20, 6),
            currency text CHECK (currency {CURRENCY}),
            authority_assertion_id uuid,
            authority_version integer,
            mapping_versions jsonb NOT NULL,
            contributing_observation_ids uuid[] NOT NULL,
            resolved_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, run_id, entity_id, property_iri),
            CHECK ((status = 'RESOLVED') = (authority_assertion_id IS NOT NULL
                AND value IS NOT NULL)),
            FOREIGN KEY (tenant_id, run_id) REFERENCES domain_runs (tenant_id, id),
            FOREIGN KEY (tenant_id, entity_id) REFERENCES domain_entities (tenant_id, id),
            FOREIGN KEY (tenant_id, authority_assertion_id)
                REFERENCES authority_assertions (tenant_id, id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE domain_relationship_instances (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants (id),
            ontology_id uuid NOT NULL,
            subject_entity_id uuid NOT NULL,
            predicate_iri text NOT NULL,
            object_entity_id uuid NOT NULL,
            provenance jsonb NOT NULL,
            created_run_id uuid NOT NULL,
            observed_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, subject_entity_id, predicate_iri, object_entity_id),
            CHECK (subject_entity_id <> object_entity_id),
            FOREIGN KEY (tenant_id, ontology_id) REFERENCES ontologies (tenant_id, id),
            FOREIGN KEY (tenant_id, subject_entity_id) REFERENCES domain_entities (tenant_id, id),
            FOREIGN KEY (tenant_id, object_entity_id) REFERENCES domain_entities (tenant_id, id),
            FOREIGN KEY (tenant_id, created_run_id) REFERENCES domain_runs (tenant_id, id)
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE domain_cases (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants (id),
            run_id uuid NOT NULL,
            case_type text NOT NULL,
            reconciliation_scope text NOT NULL
                CHECK (reconciliation_scope IN ('INTRA_ENTITY', 'INTER_ENTITY')),
            severity text NOT NULL CHECK (severity IN ('low', 'medium', 'high', 'critical')),
            status text NOT NULL DEFAULT 'OPEN' CHECK (status IN ({CASE_STATUSES})),
            fingerprint text NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{{64}}$'),
            entity_ids uuid[] NOT NULL,
            property_iris text[] NOT NULL DEFAULT '{{}}',
            relationship_ids uuid[] NOT NULL DEFAULT '{{}}',
            observation_ids uuid[] NOT NULL DEFAULT '{{}}',
            expected jsonb,
            observed jsonb,
            expected_amount numeric(20, 6),
            observed_amount numeric(20, 6),
            currency text CHECK (currency {CURRENCY}),
            evidence jsonb NOT NULL,
            method text NOT NULL,
            rule_version text NOT NULL,
            version_refs jsonb NOT NULL,
            assignee_id uuid REFERENCES users (id),
            reason text,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, id),
            CHECK ((expected_amount IS NULL AND observed_amount IS NULL) OR currency IS NOT NULL),
            FOREIGN KEY (tenant_id, run_id) REFERENCES domain_runs (tenant_id, id)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX domain_cases_open_fingerprint ON domain_cases (tenant_id, fingerprint) "
        "WHERE status NOT IN ('DISMISSED', 'CLOSED')"
    )
    op.execute("CREATE INDEX domain_cases_status_idx ON domain_cases (tenant_id, status)")

    op.execute(
        """
        CREATE FUNCTION guard_domain_run_update() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.status <> 'RUNNING' THEN
                RAISE EXCEPTION 'a finished domain run is immutable';
            END IF;
            IF (NEW.tenant_id, NEW.pack_namespace, NEW.pack_version, NEW.ontology_id,
                NEW.mapping_versions, NEW.authority_versions, NEW.policy_versions,
                NEW.rule_version, NEW.source_snapshots, NEW.inputs_hash)
               IS DISTINCT FROM
               (OLD.tenant_id, OLD.pack_namespace, OLD.pack_version, OLD.ontology_id,
                OLD.mapping_versions, OLD.authority_versions, OLD.policy_versions,
                OLD.rule_version, OLD.source_snapshots, OLD.inputs_hash) THEN
                RAISE EXCEPTION 'a domain run keeps its pinned versions';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER domain_runs_guard BEFORE UPDATE ON domain_runs
        FOR EACH ROW EXECUTE FUNCTION guard_domain_run_update()
        """
    )
    op.execute(
        """
        CREATE FUNCTION guard_resolution_review() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.status <> 'PENDING_REVIEW' OR NEW.status NOT IN ('ACCEPTED', 'REJECTED') THEN
                RAISE EXCEPTION 'only a pending identity decision can be reviewed, once';
            END IF;
            IF (NEW.tenant_id, NEW.run_id, NEW.source_record_id, NEW.class_iri, NEW.policy_id,
                NEW.policy_version, NEW.features, NEW.candidate_entity_ids)
               IS DISTINCT FROM
               (OLD.tenant_id, OLD.run_id, OLD.source_record_id, OLD.class_iri, OLD.policy_id,
                OLD.policy_version, OLD.features, OLD.candidate_entity_ids) THEN
                RAISE EXCEPTION 'a review records an outcome and changes nothing else';
            END IF;
            IF NEW.selected_entity_id IS NOT NULL
               AND NOT NEW.selected_entity_id = ANY (OLD.candidate_entity_ids) THEN
                RAISE EXCEPTION 'the selected entity must be one of the candidates';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER entity_resolution_review_guard BEFORE UPDATE ON entity_resolution_decisions
        FOR EACH ROW EXECUTE FUNCTION guard_resolution_review()
        """
    )
    # Spec 9.3. RESOLVED is reachable only from ACTION_PENDING, which only the execution path
    # enters; a failed or ambiguous execution goes back to HUMAN_REVIEW or OPEN.
    op.execute(
        """
        CREATE FUNCTION guard_case_transition() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF (NEW.tenant_id, NEW.run_id, NEW.case_type, NEW.reconciliation_scope,
                NEW.fingerprint, NEW.entity_ids, NEW.observation_ids, NEW.evidence,
                NEW.expected_amount, NEW.observed_amount, NEW.currency, NEW.version_refs)
               IS DISTINCT FROM
               (OLD.tenant_id, OLD.run_id, OLD.case_type, OLD.reconciliation_scope,
                OLD.fingerprint, OLD.entity_ids, OLD.observation_ids, OLD.evidence,
                OLD.expected_amount, OLD.observed_amount, OLD.currency, OLD.version_refs) THEN
                RAISE EXCEPTION 'a case keeps its finding';
            END IF;
            IF NEW.status IS DISTINCT FROM OLD.status AND (OLD.status, NEW.status) NOT IN (
                ('OPEN', 'INVESTIGATING'), ('INVESTIGATING', 'HUMAN_REVIEW'),
                ('HUMAN_REVIEW', 'ACTION_PENDING'), ('ACTION_PENDING', 'RESOLVED'),
                ('ACTION_PENDING', 'HUMAN_REVIEW'), ('ACTION_PENDING', 'OPEN'),
                ('OPEN', 'DISMISSED'), ('INVESTIGATING', 'DISMISSED'),
                ('HUMAN_REVIEW', 'DISMISSED'), ('RESOLVED', 'CLOSED'),
                ('DISMISSED', 'CLOSED'), ('CLOSED', 'OPEN')
            ) THEN
                RAISE EXCEPTION 'case transition % -> % is not allowed', OLD.status, NEW.status;
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER domain_cases_transition_guard BEFORE UPDATE ON domain_cases
        FOR EACH ROW EXECUTE FUNCTION guard_case_transition()
        """
    )

    for table in TENANT_TABLES:
        enable_tenant_rls(table)
    for table in APPEND_ONLY:
        op.execute(f"GRANT SELECT, INSERT ON {table} TO {APP_ROLE}")
    for table in ("domain_runs", "entity_resolution_decisions", "domain_cases"):
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON {table} TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP TRIGGER domain_cases_transition_guard ON domain_cases")
    op.execute("DROP FUNCTION guard_case_transition()")
    op.execute("DROP TRIGGER entity_resolution_review_guard ON entity_resolution_decisions")
    op.execute("DROP FUNCTION guard_resolution_review()")
    op.execute("DROP TRIGGER domain_runs_guard ON domain_runs")
    op.execute("DROP FUNCTION guard_domain_run_update()")
    for table in reversed(TENANT_TABLES):
        op.execute(f"DROP TABLE {table}")
