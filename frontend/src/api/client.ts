// Tenant selection only picks a tenant. The server proves access from membership (spec 11.1).
const TENANT_HEADER = "X-Tenant-Id";

// The error envelope ratified under OPEN-14: {"error": {"code", "message", ...}}.
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

export type ApiOptions = RequestInit & { tenantId?: string; json?: unknown };

// Auth transport (OPEN-14): the HttpOnly session cookie, which the browser sends on same-origin
// requests. The client never sees or sends a token itself.
export async function apiFetch<T>(path: string, options: ApiOptions = {}): Promise<T> {
  const { tenantId, json, headers, ...rest } = options;
  const response = await fetch(path, {
    ...rest,
    ...(json === undefined ? {} : { body: JSON.stringify(json) }),
    credentials: "same-origin",
    headers: {
      ...headers,
      ...(json === undefined ? {} : { "Content-Type": "application/json" }),
      ...(tenantId ? { [TENANT_HEADER]: tenantId } : {}),
    },
  });
  // A 204, or any empty body, has nothing to parse.
  const text = await response.text();
  const body = text ? parse(text) : undefined;
  if (!response.ok) {
    throw new ApiError(
      response.status,
      body?.error?.code ?? "UNKNOWN",
      body?.error?.message ?? response.statusText,
    );
  }
  return body as T;
}

function parse(text: string) {
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

// The text to show for a failed call. Only the envelope's message reaches the screen.
export function messageOf(error: unknown): string {
  return error instanceof ApiError ? error.message : "Something went wrong. Try again.";
}

export async function checkHealth(): Promise<boolean> {
  try {
    await apiFetch("/healthz");
    return true;
  } catch {
    return false;
  }
}
