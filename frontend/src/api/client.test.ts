import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiFetch, messageOf } from "./client";

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

  it("keeps the rejected fields of a 422 and shows them after the message", async () => {
    respond(422, {
      error: {
        code: "VALIDATION_ERROR",
        message: "Invalid request.",
        fields: [{ loc: ["body", "field_path"], msg: "String should have at least 1 character" }],
      },
    });
    const error = await apiFetch("/api/x").catch((caught: unknown) => caught);
    expect(error).toMatchObject({ fields: [{ loc: ["body", "field_path"] }] });
    expect(messageOf(error)).toBe(
      "Invalid request. field_path: String should have at least 1 character",
    );
  });

  it("has no fields when the envelope names none", async () => {
    respond(409, { error: { code: "STALE_DRAFT", message: "The draft changed." } });
    const error = await apiFetch("/api/x").catch((caught: unknown) => caught);
    expect(error).toMatchObject({ fields: [] });
    expect(messageOf(error)).toBe("The draft changed.");
  });

  it("keeps other envelope keys and shows listed failures after the message", async () => {
    respond(422, {
      error: {
        code: "PLAYBOOK_INELIGIBLE",
        message: "The playbook's preconditions do not hold.",
        failures: ["a refund already exists", "the order is disputed"],
      },
    });
    const error = await apiFetch("/api/x").catch((caught: unknown) => caught);
    expect(error).toMatchObject({
      fields: [],
      details: { failures: ["a refund already exists", "the order is disputed"] },
    });
    expect(messageOf(error)).toBe(
      "The playbook's preconditions do not hold. a refund already exists; the order is disputed",
    );
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
