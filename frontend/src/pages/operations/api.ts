import { apiFetch } from "../../api/client";
import type {
  DecisionInput,
  DomainCase,
  DomainRun,
  EntityDetail,
  EntitySummary,
  ResolutionDecision,
  TeamMember,
  TriageInput,
} from "./types";

function query(params: Record<string, string | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value) search.set(key, value);
  const text = search.toString();
  return text ? `?${text}` : "";
}

// The domain routes take the tenant from X-Tenant-Id; none has a tenant path.
export function operationsApi(tenantId: string) {
  const call = <T>(path: string, init: { method?: string; json?: unknown } = {}) =>
    apiFetch<T>(path, { ...init, tenantId });
  return {
    runs: () => call<DomainRun[]>("/api/domain-runs"),
    startRun: () => call<DomainRun>("/api/domain-runs", { method: "POST" }),
    cases: (filter: { status?: string; case_type?: string }) =>
      call<DomainCase[]>(`/api/domain-cases${query(filter)}`),
    triage: (caseId: string, body: TriageInput) =>
      call<DomainCase>(`/api/domain-cases/${caseId}`, { method: "PATCH", json: body }),
    reopen: (caseId: string, reason: string) =>
      call<DomainCase>(`/api/domain-cases/${caseId}/reopen`, { method: "POST", json: { reason } }),
    entities: (classIri?: string) =>
      call<EntitySummary[]>(`/api/entities${query({ class_iri: classIri })}`),
    entity: (entityId: string) => call<EntityDetail>(`/api/entities/${entityId}`),
    pendingDecisions: () => call<ResolutionDecision[]>("/api/entity-resolution"),
    decide: (decisionId: string, body: DecisionInput) =>
      call(`/api/entity-resolution/${decisionId}/decision`, { method: "POST", json: body }),
    members: () => apiFetch<TeamMember[]>(`/api/tenants/${tenantId}/members`),
  };
}

export type OperationsApi = ReturnType<typeof operationsApi>;
