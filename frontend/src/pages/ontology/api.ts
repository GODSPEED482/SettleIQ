import { ApiError, apiFetch } from "../../api/client";
import type {
  Authority,
  AuthorityInput,
  DomainPack,
  Draft,
  GenerationJob,
  JsonLdNode,
  Mapping,
  MappingInput,
  PublishedOntology,
  PublishedVersion,
  SourceSummary,
  ValidationRun,
} from "./types";

// The ontology routes take the tenant from X-Tenant-Id; none has a tenant path.
export function ontologyApi(tenantId: string) {
  const call = <T>(path: string, init: { method?: string; json?: unknown } = {}) =>
    apiFetch<T>(path, { ...init, tenantId });
  return {
    published: () => call<PublishedOntology>("/api/ontology"),
    packs: () => call<DomainPack[]>("/api/domain-packs"),
    managePack: (namespace: string, action: "ACTIVATE" | "DISABLE") =>
      call("/api/domain-packs", { method: "POST", json: { namespace, action } }),
    // No open draft is a 404, which here only means there is nothing to edit.
    openDraft: () =>
      call<Draft>("/api/ontology/drafts/open").catch((caught: unknown) => {
        if (caught instanceof ApiError && caught.status === 404) return null;
        throw caught;
      }),
    createDraft: () => call<Draft>("/api/ontology/drafts", { method: "POST" }),
    editDraft: (draft: Draft, graph: JsonLdNode[]) =>
      call<Draft>(`/api/ontology/drafts/${draft.id}`, {
        method: "PATCH",
        json: { expected_revision: draft.revision, graph },
      }),
    abandonDraft: (draftId: string) =>
      call(`/api/ontology/drafts/${draftId}/abandon`, { method: "POST" }),
    generate: () => call<GenerationJob>("/api/ontology/generate", { method: "POST" }),
    generationJob: (jobId: string) => call<GenerationJob>(`/api/ontology/generation-jobs/${jobId}`),
    validate: (draftId: string) =>
      call<ValidationRun>("/api/ontology/validate", { method: "POST", json: { draft_id: draftId } }),
    publish: (draftId: string, validationRunId: string) =>
      call<PublishedVersion>("/api/ontology/publish", {
        method: "POST",
        json: { draft_id: draftId, validation_run_id: validationRunId, confirm_publish: true },
      }),
    mappings: (draftId: string) =>
      call<Mapping[]>(`/api/mappings?draft_id=${encodeURIComponent(draftId)}`),
    saveMapping: (body: MappingInput) => call("/api/mappings", { method: "POST", json: body }),
    retireMapping: (id: string) => call(`/api/mappings/${id}/retire`, { method: "POST" }),
    authority: (draftId: string) =>
      call<Authority[]>(`/api/authority?draft_id=${encodeURIComponent(draftId)}`),
    saveAuthority: (body: AuthorityInput) => call("/api/authority", { method: "POST", json: body }),
    retireAuthority: (id: string) => call(`/api/authority/${id}/retire`, { method: "POST" }),
    sources: () => call<SourceSummary[]>("/api/sources"),
  };
}

export type OntologyApi = ReturnType<typeof ontologyApi>;
