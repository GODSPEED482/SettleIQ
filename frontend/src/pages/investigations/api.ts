import { apiFetch } from "../../api/client";
import type { DomainCase } from "../operations/types";
import type { Investigation, InvestigationStarted, InvestigationSummary } from "./types";

// The investigation routes take the tenant from X-Tenant-Id (backend/app/api/investigation.py).
export function investigationsApi(tenantId: string) {
  const call = <T>(path: string, init: { method?: string } = {}) =>
    apiFetch<T>(path, { ...init, tenantId });
  return {
    cases: () => call<DomainCase[]>("/api/domain-cases"),
    list: (caseId?: string) =>
      call<InvestigationSummary[]>(
        `/api/investigations${caseId ? `?case_id=${encodeURIComponent(caseId)}` : ""}`,
      ),
    read: (id: string) => call<Investigation>(`/api/investigations/${id}`),
    start: (caseId: string) =>
      call<InvestigationStarted>(`/api/agent/investigate/${caseId}`, { method: "POST" }),
  };
}

export type InvestigationsApi = ReturnType<typeof investigationsApi>;
