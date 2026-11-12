// Shapes returned by the ontology, mapping and authority routes (spec 12.2). Terms are JSON-LD
// nodes, so the Studio stays free of any one pack's entity list.

export type IriValue = string | { "@id": string };

export type JsonLdNode = {
  "@id": string;
  "@type"?: string | string[];
  "rdfs:label"?: string;
  "rdfs:comment"?: string;
  "rdfs:subClassOf"?: IriValue | IriValue[];
  "rdfs:domain"?: IriValue;
  "rdfs:range"?: IriValue;
  [key: string]: unknown;
};

export type OntologyArtifact = {
  layer: "CORE" | "PACK";
  namespace: string;
  version: string;
  content_hash: string;
  content: { "@context"?: unknown; "@graph": JsonLdNode[] };
};

export type TenantExtension = { "@context"?: unknown; "@graph": JsonLdNode[] };

export type PublishedOntology = {
  version: number | null;
  content_hash?: string;
  published_at?: string;
  artifacts: OntologyArtifact[];
  tenant_extension: TenantExtension | null;
};

export type Basis = "observed" | "inferred";

export type ProposalEvidence = {
  kind: "foreign_key" | "field_profile" | "name_similarity";
  field: string;
  // Server text for observed evidence; the model's reading is kept apart in `rationale`.
  detail: string;
  rationale?: string;
  basis: Basis;
};

export type TermProposal = {
  type: "term";
  id: string;
  kind: "class" | "property" | "relationship";
  label: string;
  subclass_of: string | null;
  domain: string | null;
  range: string | null;
  status: string;
  basis: Basis;
  evidence: ProposalEvidence[];
};

export type BindingProposal = {
  type: "binding";
  source_id: string;
  object: string;
  field: string;
  property: string;
  confidence: "low" | "medium" | "high";
  status: string;
  basis: Basis;
  evidence: ProposalEvidence[];
};

export type Proposal = TermProposal | BindingProposal;

export type Draft = {
  id: string;
  revision: number;
  base_version: number | null;
  status: string;
  content_hash: string;
  artifacts: OntologyArtifact[];
  tenant_extension: TenantExtension;
  proposals: Proposal[];
};

export type DomainPack = {
  namespace: string;
  version: string;
  status: "ACTIVE" | "DISABLED" | "AVAILABLE" | string;
  case_types: string[];
};

export type GenerationJob = {
  job_id: string;
  status: "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED" | string;
  error: string | null;
};

export type TransformName = "identity" | "decimal_string" | "upper" | "lower" | "iso8601" | "enum_map";

export type MappingInput = {
  draft_id: string;
  source_id: string;
  external_object: string;
  field_path: string;
  property_iri: string;
  class_iri: string;
  datatype: "decimal" | "string" | "datetime" | "boolean" | "integer";
  transform: { name: TransformName; values?: Record<string, string> };
  nullable: boolean;
  is_unique: boolean;
  sync_strategy: "batch" | "event" | "file";
  value_basis: string;
  unit: string | null;
  currency_field: string | null;
  temporal_context: string;
  method: "human" | "accepted_proposal";
};

export type Mapping = Omit<MappingInput, "draft_id"> & { id: string; version: number };

export type AuthorityInput = {
  draft_id: string;
  property_iri: string;
  entity_type_iri: string;
  source_id: string;
  valid_from: string;
  valid_to: string | null;
  verification_method: string;
};

export type Authority = Omit<AuthorityInput, "draft_id"> & { id: string; version: number };

export type ValidatorResult = {
  validator: string;
  kind: "platform" | "shacl" | "pack";
  passed: boolean;
  messages: string[];
};

export type ValidationRun = {
  validation_run_id: string;
  draft_id: string;
  revision: number;
  content_hash: string;
  passed: boolean;
  results: ValidatorResult[];
};

export type PublishedVersion = {
  id: string;
  version: number;
  content_hash: string;
  validation_run_id: string;
  published_at: string;
  relationships: number;
};

export type SourceSummary = {
  id: string;
  display_name: string;
  connector_id: string;
  status: string;
};
