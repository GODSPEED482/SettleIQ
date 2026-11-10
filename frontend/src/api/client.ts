// Tenant selection only picks a tenant. The server proves access from membership (spec 11.1).
const TENANT_HEADER = "X-Tenant-Id";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

// Auth transport (OPEN-14): the HttpOnly session cookie, which the browser sends on same-origin
// requests. The client never sees or sends a token itself.
// TODO(G1 1.11 Suprava): response.json() throws on an empty 204; handle no-content responses.
export async function apiFetch<T>(
  path: string,
  options: RequestInit & { tenantId?: string } = {},
): Promise<T> {
  const { tenantId, headers, ...rest } = options;
  const response = await fetch(path, {
    ...rest,
    credentials: "same-origin",
    headers: { ...headers, ...(tenantId ? { [TENANT_HEADER]: tenantId } : {}) },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new ApiError(
      response.status,
      body?.error?.code ?? "UNKNOWN",
      body?.error?.message ?? response.statusText,
    );
  }
  return response.json() as Promise<T>;
}

export async function checkHealth(): Promise<boolean> {
  try {
    await apiFetch("/healthz");
    return true;
  } catch {
    return false;
  }
}
