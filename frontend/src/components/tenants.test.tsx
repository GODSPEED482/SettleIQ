// @vitest-environment jsdom
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CreateTenant } from "../pages/CreateTenant";
import { PermissionsProvider, usePermissions } from "../permissions/PermissionsContext";
import { SessionProvider, useSession } from "../session/SessionContext";
import { mockApi } from "../test/api";
import { stubLocalStorage } from "../test/storage";
import { TenantPicker } from "./TenantPicker";

const A = { id: "a0000000-0000-4000-8000-000000000000", name: "Acme" };
const B = { id: "b0000000-0000-4000-8000-000000000000", name: "Beta" };
const tenantsReply = (...tenants: (typeof A)[]) => ({ status: 200, body: { tenants } });
const permissionsFor = (id: string) => `GET /api/me/permissions?tenant_id=${id}`;
const NO_PERMISSIONS = { status: 200, body: { permissions: [] } };

beforeEach(() => {
  stubLocalStorage();
});
afterEach(() => vi.unstubAllGlobals());

function Probe() {
  const { tenantId, status } = usePermissions();
  const { signOut } = useSession();
  const location = useLocation();
  return (
    <>
      <p>tenant {tenantId ?? "none"}</p>
      <p>permissions {status}</p>
      <p>at {location.pathname}</p>
      <button onClick={() => void signOut()}>out</button>
    </>
  );
}

function renderApp(path = "/") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <SessionProvider>
        <PermissionsProvider>
          <TenantPicker />
          <Routes>
            <Route path="/tenants/new" element={<CreateTenant />} />
            <Route path="*" element={null} />
          </Routes>
          <Probe />
        </PermissionsProvider>
      </SessionProvider>
    </MemoryRouter>,
  );
}

describe("tenant selection", () => {
  it("selects the only tenant and loads its permissions", async () => {
    const api = mockApi({
      "GET /api/me/tenants": tenantsReply(A),
      [permissionsFor(A.id)]: NO_PERMISSIONS,
    });
    renderApp();
    expect(await screen.findByText(`tenant ${A.id}`)).toBeTruthy();
    expect(await screen.findByText("permissions ready")).toBeTruthy();
    expect(api.calls.map((call) => call.key)).toContain(permissionsFor(A.id));
  });

  it("waits for a choice among several, then remembers it", async () => {
    mockApi({
      "GET /api/me/tenants": tenantsReply(A, B),
      [permissionsFor(B.id)]: NO_PERMISSIONS,
    });
    renderApp();
    const picker = await screen.findByRole("combobox");
    expect(screen.getByText("tenant none")).toBeTruthy();
    fireEvent.change(picker, { target: { value: B.id } });
    expect(await screen.findByText(`tenant ${B.id}`)).toBeTruthy();
    expect(localStorage.getItem("settleiq.tenant")).toBe(B.id);
  });

  it("ignores a remembered tenant the session no longer lists", async () => {
    localStorage.setItem("settleiq.tenant", "c0000000-0000-4000-8000-000000000000");
    mockApi({ "GET /api/me/tenants": tenantsReply(A, B) });
    renderApp();
    await screen.findByRole("combobox");
    expect(screen.getByText("tenant none")).toBeTruthy();
  });

  it("clears the tenant on sign-out", async () => {
    localStorage.setItem("settleiq.tenant", A.id);
    mockApi({
      "GET /api/me/tenants": [tenantsReply(A, B), { status: 401 }],
      [permissionsFor(A.id)]: NO_PERMISSIONS,
      "POST /api/auth/sign-out": { status: 200, body: { status: "signed_out" } },
    });
    renderApp();
    expect(await screen.findByText(`tenant ${A.id}`)).toBeTruthy();
    await act(async () => fireEvent.click(screen.getByText("out")));
    await waitFor(() => expect(screen.getByText("tenant none")).toBeTruthy());
    expect(localStorage.getItem("settleiq.tenant")).toBeNull();
  });
});

describe("CreateTenant", () => {
  it("sends only the name, then selects the new tenant", async () => {
    const api = mockApi({
      "GET /api/me/tenants": [tenantsReply(A), tenantsReply(A, B)],
      "POST /api/tenants": { status: 201, body: { id: B.id, name: B.name } },
      [permissionsFor(B.id)]: NO_PERMISSIONS,
    });
    renderApp("/tenants/new");
    fireEvent.change(await screen.findByLabelText("Business name"), { target: { value: "Beta" } });
    fireEvent.click(screen.getByRole("button", { name: "Create tenant" }));
    expect(await screen.findByText(`tenant ${B.id}`)).toBeTruthy();
    expect(screen.getByText("at /team")).toBeTruthy();
    expect(api.sent("POST /api/tenants")).toEqual([{ name: "Beta" }]);
  });
});
