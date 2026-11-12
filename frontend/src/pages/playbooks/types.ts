// Shapes returned by backend/app/playbooks/gateway.py, backend/app/execution/worker.py and
// backend/app/execution/readiness.py. These list only the fields the screens read.

export type RequestStatus =
  | "CREATED"
  | "REQUEST_POLICY_EVALUATED"
  | "NEEDS_APPROVAL"
  | "APPROVED"
  | "PRECONDITIONS_VALIDATED"
  | "EXECUTION_POLICY_ALLOWED"
  | "EXECUTION_CREATED"
  | "DENIED"
  | "REJECTED"
  | "EXPIRED"
  | "CANCELLED";

// The request moves through these on its own, inside the call that put it there or the next.
export const TRANSIENT_REQUEST: RequestStatus[] = [
  "CREATED",
  "REQUEST_POLICY_EVALUATED",
  "APPROVED",
  "EXECUTION_POLICY_ALLOWED",
];

export type PolicyDecision = {
  id: string;
  stage: "REQUEST" | "EXECUTION";
  parent_decision_id: string | null;
  policy_version: string;
  approval_required: boolean;
  decision: string;
  conditions: { proof?: Record<string, unknown> } & Record<string, unknown>;
  reason: string | null;
  created_at: string;
};

export type Approval = {
  id: string;
  approver_user_id: string;
  decision: "APPROVED" | "REJECTED";
  reason: string | null;
  created_at: string;
};

export type AuditRef = { id: string; action: string; decision: string | null; created_at: string };

export type PlaybookRequest = {
  id: string;
  case_id: string;
  playbook_id: string;
  playbook_version: string;
  pack_version: string;
  connector_id: string;
  action_capability: string;
  requested_by: string;
  parameters: Record<string, unknown>;
  target: Record<string, unknown>;
  status: RequestStatus;
  created_at: string;
  updated_at: string;
  decisions: PolicyDecision[];
  approval: Approval | null;
  execution: { id: string; status: string } | null;
  requested_by_me: boolean;
  audit_refs: AuditRef[];
};

export const TERMINAL_EXECUTION = ["SUCCEEDED", "FAILED", "MANUAL_REVIEW", "POLICY_BLOCKED"];

export type Attempt = {
  sequence: number;
  connector_id: string;
  action_capability: string;
  request_evidence: unknown;
  response_evidence: unknown;
  status: string;
  error_class: string | null;
  started_at: string;
  finished_at: string | null;
};

export type Execution = {
  id: string;
  request_id: string;
  status: string;
  playbook_id: string;
  connector_id: string;
  action_capability: string;
  target: Record<string, unknown>;
  idempotency_key: string;
  reason: string | null;
  result_ref: { readback?: string; evidence?: unknown; evidence_hash?: string } | null;
  started_at: string | null;
  completed_at: string | null;
  attempts: Attempt[];
};

export type Readiness = {
  domain_pack_id: string;
  connector_id: string;
  action_capability: string;
  mode: "SHADOW" | "LIVE";
};

export type AuditRecord = {
  id: string;
  action: string;
  permission_key: string | null;
  decision: string | null;
  detail: unknown;
  created_at: string;
};
