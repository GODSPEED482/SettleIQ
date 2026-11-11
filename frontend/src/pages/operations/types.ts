// Shapes returned by the domain routes in backend/app/domain (runs.py, cases.py, entities.py).
// The API returns untyped objects, so these list only the fields the screens read.

export type Provenance =
  | "provider_api"
  | "provider_event"
  | "provider_report"
  | "synthetic_reference"
  | "tenant_file"
  | "database";

export type DomainRun = {
  id: string;
  pack_namespace: string;
  pack_version: string;
  ontology_id: string;
  ontology_version: number;
  ontology_content_hash: string;
  rule_version: string;
  inputs_hash: string | null;
  status: "RUNNING" | "SUCCEEDED" | "FAILED";
  findings: { case_type: string; scope: string; case_id: string; new_case: boolean }[] | null;
  failure: string | null;
  started_at: string;
  finished_at: string | null;
};

export type CaseStatus =
  | "OPEN"
  | "INVESTIGATING"
  | "HUMAN_REVIEW"
  | "ACTION_PENDING"
  | "RESOLVED"
  | "DISMISSED"
  | "CLOSED";

export type DomainCase = {
  id: string;
  run_id: string;
  case_type: string;
  reconciliation_scope: string;
  severity: string;
  status: CaseStatus;
  expected_amount: string | null;
  observed_amount: string | null;
  currency: string | null;
  assignee_id: string | null;
  reason: string | null;
  evidence_provenance: Provenance[];
  created_at: string;
};

// The moves PATCH /api/domain-cases/{id} accepts (backend/app/domain/cases.py).
export const TRIAGE_TRANSITIONS: Partial<Record<CaseStatus, CaseStatus[]>> = {
  OPEN: ["INVESTIGATING", "DISMISSED"],
  INVESTIGATING: ["HUMAN_REVIEW", "DISMISSED"],
  HUMAN_REVIEW: ["DISMISSED"],
  RESOLVED: ["CLOSED"],
  DISMISSED: ["CLOSED"],
};
export const NEEDS_REASON: CaseStatus[] = ["DISMISSED", "CLOSED"];

export type TriageInput = {
  status?: CaseStatus;
  assignee_id?: string;
  comment?: string;
  reason?: string;
};

export type EntitySummary = {
  id: string;
  class_iri: string;
  canonical_key: string;
};

export type Observation = {
  id: string;
  property_iri: string;
  source_id: string;
  field_path: string;
  raw_value: string | null;
  normalized_value: string | null;
  currency: string | null;
  value_basis: string;
  provenance: Provenance | null;
  observed_at: string;
};

export type ResolvedStatus =
  | "RESOLVED"
  | "CONFLICT"
  | "UNRESOLVED"
  | "STALE"
  | "HUMAN_REVIEW"
  | "AUTHORITY_CONFLICT";

export type ResolvedView = {
  run_id: string;
  property_iri: string;
  status: ResolvedStatus;
  value: string | null;
  currency: string | null;
  contributing_observation_ids: string[];
};

export type EntityDetail = EntitySummary & {
  observations: Observation[];
  resolved: ResolvedView[];
};

export type ResolutionDecision = {
  id: string;
  source_record_id: string;
  class_iri: string;
  features: Record<string, unknown>;
  candidate_entity_ids: string[];
  status: string;
  created_at: string;
};

export type DecisionInput =
  | { outcome: "ACCEPT"; candidate_entity_id: string; reason: string }
  | { outcome: "REJECT"; reason: string };

export type TeamMember = { user_id: string; email: string; status: string };
