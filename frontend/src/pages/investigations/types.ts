// Shapes returned by backend/app/investigation/service.py. The result is the agent answer the
// dispatcher validated against backend/app/investigation/response.py before storing it.

export type InvestigationStatus = "RUNNING" | "SUCCEEDED" | "FAILED";

export type InvestigationSummary = {
  investigation_id: string;
  case_id: string;
  status: InvestigationStatus;
  proposed_classification: string | null;
  recommended_playbook: string | null;
  confidence: string | null;
  error: string | null;
  created_at: string;
  finished_at: string | null;
};

export type EvidenceReference = {
  evidence_id: string;
  source_id: string;
  record_id: string;
  fact: string;
};

export type InvestigationResult = {
  case_id: string;
  classification: string;
  root_cause_summary: string;
  evidence: EvidenceReference[];
  recommended_playbook: string | null;
  requires_approval: boolean;
  confidence: "low" | "medium" | "high";
  missing_evidence: string[];
};

export type ToolCall = {
  id: string;
  tool: string;
  tool_version: string;
  permission: string;
  input: unknown;
  output: unknown;
  output_hash: string | null;
  evidence_ids: string[];
  outcome: string;
  error: string | null;
  created_at: string;
};

export type Investigation = InvestigationSummary & {
  model: string | null;
  prompt_version: string | null;
  tool_registry_version: string;
  result: InvestigationResult | null;
  tool_calls: ToolCall[];
};

export type InvestigationStarted = { investigation_id: string; status: "RUNNING" };
