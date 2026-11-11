// Tenant selection only picks a tenant. The server proves access from membership (spec 11.1).
const TENANT_HEADER = "X-Tenant-Id";

// A 422 VALIDATION_ERROR names each rejected body field.
export type FieldError = { loc: (string | number)[]; msg: string };

// The error envelope ratified under OPEN-14: {"error": {"code", "message", ...}}.
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly fields: FieldError[] = [],
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
      Array.isArray(body?.error?.fields) ? body.error.fields : [],
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

// The text to show for a failed call. Only the envelope's message reaches the screen, followed
// by the rejected fields of a 422, named by their last path segment.
export function messageOf(error: unknown): string {
  if (!(error instanceof ApiError)) return "Something went wrong. Try again.";
  if (error.fields.length === 0) return error.message;
  const details = error.fields.map((field) => `${field.loc[field.loc.length - 1]}: ${field.msg}`);
  return `${error.message} ${details.join("; ")}`;
}

export async function checkHealth(): Promise<boolean> {
  try {
    await apiFetch("/healthz");
    return true;
  } catch {
    return false;
  }
}
