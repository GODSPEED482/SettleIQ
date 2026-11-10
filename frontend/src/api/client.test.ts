import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiFetch } from "./client";

function respond(status: number, body?: unknown) {
  const fetch = vi.fn(async () =>
    new Response(body === undefined ? null : JSON.stringify(body), { status }),
  );
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("apiFetch", () => {
  it("returns the parsed body", async () => {
    respond(200, { status: "ok" });
    await expect(apiFetch("/healthz")).resolves.toEqual({ status: "ok" });
  });

  it("returns undefined for 204 No Content", async () => {
    respond(204);
    await expect(apiFetch("/api/thing", { method: "DELETE" })).resolves.toBeUndefined();
  });

  it("raises the envelope's code and message", async () => {
    respond(403, { error: { code: "MISSING_PERMISSION", message: "Permission required." } });
    const error = await apiFetch("/api/x").catch((caught: ApiError) => caught);
    expect(error).toBeInstanceOf(ApiError);
    if (!(error instanceof ApiError)) return;
    expect([error.status, error.code, error.message]).toEqual([
      403,
      "MISSING_PERMISSION",
      "Permission required.",
    ]);
  });

  it("raises UNKNOWN when the error has no envelope", async () => {
    respond(502);
    await expect(apiFetch("/api/x")).rejects.toMatchObject({ status: 502, code: "UNKNOWN" });
  });

  it("sends the cookie, the tenant selector and a JSON body", async () => {
    const fetch = respond(201, {});
    await apiFetch("/api/x", { method: "POST", tenantId: "t-1", json: { name: "A" } });
    const [, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(init.credentials).toBe("same-origin");
    expect(init.body).toBe('{"name":"A"}');
    expect(init.headers).toMatchObject({
      "Content-Type": "application/json",
      "X-Tenant-Id": "t-1",
    });
  });
});
