import { vi } from "vitest";

export type Reply = { status: number; body?: unknown };
type Handler = Reply | ((init: RequestInit) => Reply);

const NOT_FOUND: Reply = {
  status: 404,
  body: { error: { code: "NOT_FOUND", message: "Resource not found." } },
};

/**
 * Stubs fetch with replies keyed by "METHOD /path?query". A list replies in order and then
 * repeats its last entry. Unmatched requests get the canonical 404.
 */
export function mockApi(routes: Record<string, Handler | Handler[]>) {
  const calls: { key: string; init: RequestInit }[] = [];
  const used = new Map<string, number>();
  const fetch = vi.fn(async (path: string, init: RequestInit = {}) => {
    const key = `${init.method ?? "GET"} ${path}`;
    calls.push({ key, init });
    const route = routes[key];
    let handler: Handler | undefined = route as Handler | undefined;
    if (Array.isArray(route)) {
      const index = used.get(key) ?? 0;
      used.set(key, index + 1);
      handler = route[Math.min(index, route.length - 1)];
    }
    const reply = !handler ? NOT_FOUND : typeof handler === "function" ? handler(init) : handler;
    const body = reply.body === undefined ? null : JSON.stringify(reply.body);
    return new Response(body, { status: reply.status });
  });
  vi.stubGlobal("fetch", fetch);
  return {
    calls,
    sent(key: string) {
      return calls.filter((call) => call.key === key).map((call) => parse(call.init.body));
    },
  };
}

function parse(body: RequestInit["body"]) {
  return typeof body === "string" ? JSON.parse(body) : undefined;
}

export function apiError(status: number, code: string, message = code): Reply {
  return { status, body: { error: { code, message } } };
}
