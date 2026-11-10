// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SessionProvider } from "../session/SessionContext";
import { apiError, mockApi, type Reply } from "../test/api";
import { stubLocalStorage } from "../test/storage";
import { PermissionsProvider } from "./PermissionsContext";
import { RequirePermission } from "./RequirePermission";

const A = { id: "a0000000-0000-4000-8000-000000000000", name: "Acme" };
const PERMISSIONS = `GET /api/me/permissions?tenant_id=${A.id}`;
const held = (key: string, tenant_wide = true) => {
  const scopes = tenant_wide ? [] : [{ type: "source", id: "s" }];
  return { status: 200, body: { permissions: [{ key, tenant_wide, scopes, denied_scopes: [] }] } };
};

beforeEach(() => {
  stubLocalStorage();
});
afterEach(() => vi.unstubAllGlobals());

function renderGate(tenants: (typeof A)[], permissions?: Reply) {
  mockApi({
    "GET /api/me/tenants": { status: 200, body: { tenants } },
    ...(permissions ? { [PERMISSIONS]: permissions } : {}),
  });
  render(
    <MemoryRouter>
      <SessionProvider>
        <PermissionsProvider>
          <RequirePermission permission="source.read">workspace</RequirePermission>
        </PermissionsProvider>
      </SessionProvider>
    </MemoryRouter>,
  );
}

describe("RequirePermission", () => {
  it("asks for a tenant before anything else", async () => {
    renderGate([]);
    expect(await screen.findByRole("heading", { name: "Choose a tenant" })).toBeTruthy();
  });

  it("explains a missing permission instead of rendering the workspace", async () => {
    renderGate([A], held("tenant.read"));
    expect(await screen.findByRole("heading", { name: "Not available" })).toBeTruthy();
    expect(screen.getByText("source.read")).toBeTruthy();
    expect(screen.queryByText("workspace")).toBeNull();
  });

  it("opens for a tenant-wide or scoped holder", async () => {
    renderGate([A], held("source.read", false));
    expect(await screen.findByText("workspace")).toBeTruthy();
  });

  it("says so when permissions cannot load", async () => {
    renderGate([A], apiError(404, "NOT_FOUND"));
    expect(await screen.findByText(/could not be loaded/)).toBeTruthy();
  });
});
