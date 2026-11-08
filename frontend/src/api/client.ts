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

// TODO(G1 OPEN-14 Suprava): settle the auth transport with the 1.9 owner, then send it from here.
// TODO(G1 1.11 Suprava): response.json() throws on an empty 204; handle no-content responses.
export async function apiFetch<T>(
  path: string,
  options: RequestInit & { tenantId?: string } = {},
): Promise<T> {
  const { tenantId, headers, ...rest } = options;
  const response = await fetch(path, {
    ...rest,
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
