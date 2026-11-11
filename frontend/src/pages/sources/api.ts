import { apiFetch, type ApiOptions } from "../../api/client";
import type {
  ConnectorManifest,
  CreateSourceInput,
  DiscoverResult,
  FileKind,
  Source,
  SyncResult,
  UploadResult,
  WebhookConfig,
} from "./types";

// The source routes take the tenant from X-Tenant-Id; none has a tenant path.
export function sourcesApi(tenantId: string) {
  const call = <T>(path: string, init: ApiOptions = {}) => apiFetch<T>(path, { ...init, tenantId });
  return {
    connectors: () => call<ConnectorManifest[]>("/api/connectors"),
    sources: () => call<Source[]>("/api/sources"),
    create: (body: CreateSourceInput) =>
      call<Source>("/api/sources", { method: "POST", json: body }),
    discover: (sourceId: string) =>
      call<DiscoverResult>(`/api/sources/${sourceId}/discover`, { method: "POST" }),
    sync: (sourceId: string) =>
      call<SyncResult>(`/api/sources/${sourceId}/sync`, { method: "POST" }),
    // Multipart: the browser sets the boundary, so no Content-Type is given here.
    upload: (sourceId: string, file: File, kind: FileKind) => {
      const form = new FormData();
      form.append("file", file);
      form.append("kind", kind);
      return call<UploadResult>(`/api/sources/${sourceId}/files`, { method: "POST", body: form });
    },
    setCredential: (sourceId: string, secretReference: string) =>
      call<Source>(`/api/sources/${sourceId}/credentials`, {
        method: "PUT",
        json: { secret_reference: secretReference },
      }),
    registerWebhook: (sourceId: string, body: WebhookConfig) =>
      call<Source>(`/api/sources/${sourceId}/webhooks`, { method: "PUT", json: body }),
  };
}

export type SourcesApi = ReturnType<typeof sourcesApi>;

// The event types a source's pinned webhooks.receive contract accepts, or null without one.
export function webhookEventTypes(source: Source): string[] | null {
  const capability = source.capabilities.find((item) => item.key === "webhooks.receive");
  if (!capability) return null;
  const properties = capability.parameters.properties as
    | { event_types?: { items?: { enum?: unknown } } }
    | undefined;
  const values = properties?.event_types?.items?.enum;
  return Array.isArray(values) ? values.filter((value): value is string => typeof value === "string") : [];
}
