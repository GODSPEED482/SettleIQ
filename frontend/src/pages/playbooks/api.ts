import { apiFetch } from "../../api/client";
import { can, type EffectivePermission } from "../../permissions/can";
import type { InvestigationSummary } from "../investigations/types";
import type { AuditRecord, Execution, PlaybookRequest, Readiness } from "./types";

function query(params: Record<string, string | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value) search.set(key, value);
  const text = search.toString();
  return text ? `?${text}` : "";
}

// The playbook routes take the tenant from X-Tenant-Id; readiness sits under the tenant path.
export function playbooksApi(tenantId: string) {
  const call = <T>(path: string, init: Parameters<typeof apiFetch>[1] = {}) =>
    apiFetch<T>(path, { ...init, tenantId });
  return {
    list: (filter: { status?: string; case_id?: string }) =>
      call<PlaybookRequest[]>(`/api/playbook-requests${query(filter)}`),
    read: (id: string) => call<PlaybookRequest>(`/api/playbook-requests/${id}`),
    // Spec 12.1: only the case and the playbook's declared parameters. The server derives the
    // requester, the approval requirement, the target and every decision.
    create: (playbookId: string, caseId: string, idempotencyKey: string) =>
      call<PlaybookRequest>(`/api/playbooks/${encodeURIComponent(playbookId)}/requests`, {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey },
        json: { case_id: caseId, parameters: {} },
      }),
    approve: (id: string, reason: string) =>
      call<PlaybookRequest>(`/api/playbook-requests/${id}/approve`, {
        method: "POST",
        json: reason ? { reason } : {},
      }),
    reject: (id: string, reason: string) =>
      call<PlaybookRequest>(`/api/playbook-requests/${id}/reject`, {
        method: "POST",
        json: { reason },
      }),
    execution: (id: string) => call<Execution>(`/api/executions/${id}`),
    audit: (id: string) => call<AuditRecord>(`/api/audit/${id}`),
    readiness: () => call<Readiness[]>(`/api/tenants/${tenantId}/execution-readiness`),
    investigations: (caseId: string) =>
      call<InvestigationSummary[]>(`/api/investigations${query({ case_id: caseId })}`),
  };
}

export type PlaybooksApi = ReturnType<typeof playbooksApi>;

// A playbook key is held tenant-wide, for the playbook, or for its pack. A denial of the
// playbook itself wins over a pack grant.
export function canOnPlaybook(
  permissions: EffectivePermission[],
  key: string,
  playbookId: string,
): boolean {
  const playbook = { type: "playbook", id: playbookId };
  if (can(permissions, key, playbook)) return true;
  const held = permissions.find((permission) => permission.key === key);
  const denied = held?.denied_scopes.some(
    (scope) => scope.type === playbook.type && scope.id === playbook.id,
  );
  const pack = { type: "domain_pack", id: playbookId.split(":")[0] };
  return !denied && can(permissions, key, pack);
}

// One key per form, so a resubmission returns the request already made (OPEN-14).
export function newIdempotencyKey(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
}

// This build runs PayPal in Sandbox only (task 4.10); other connectors are named as they are.
export const environmentOf = (connectorId: string) =>
  connectorId === "paypal" ? "PayPal Sandbox" : connectorId;
