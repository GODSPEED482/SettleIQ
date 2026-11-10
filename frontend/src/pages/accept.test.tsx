// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PermissionsProvider, usePermissions } from "../permissions/PermissionsContext";
import { RequireSession } from "../session/RequireSession";
import { SessionProvider } from "../session/SessionContext";
import { apiError, mockApi, type Reply } from "../test/api";
import { stubLocalStorage } from "../test/storage";
import { AcceptInvitation } from "./AcceptInvitation";

const T = "a0000000-0000-4000-8000-000000000000";
const U = "b0000000-0000-4000-8000-000000000000";
const tenants = (...ids: string[]): Reply => ({
  status: 200,
  body: { tenants: ids.map((id) => ({ id, name: id })) },
});

beforeEach(() => {
  stubLocalStorage();
});
afterEach(() => vi.unstubAllGlobals());

function Where() {
  const location = useLocation();
  const { tenantId } = usePermissions();
  const from = (location.state as { from?: string } | null)?.from;
  return (
    <p>
      at {location.pathname} from {from ?? "-"} tenant {tenantId ?? "none"}
    </p>
  );
}

function renderAt(path: string) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <SessionProvider>
        <PermissionsProvider>
          <Routes>
            <Route
              path="/invitations/accept"
              element={
                <RequireSession>
                  <AcceptInvitation />
                </RequireSession>
              }
            />
            <Route path="*" element={<Where />} />
          </Routes>
        </PermissionsProvider>
      </SessionProvider>
    </MemoryRouter>,
  );
}

describe("AcceptInvitation", () => {
  it("sends a signed-out visitor to sign in, keeping the link to come back to", async () => {
    mockApi({ "GET /api/me/tenants": { status: 401 } });
    renderAt("/invitations/accept#token=abc");
    expect(
      await screen.findByText("at /sign-in from /invitations/accept#token=abc tenant none"),
    ).toBeTruthy();
  });

  it("joins on an explicit click, then selects the new tenant", async () => {
    const api = mockApi({
      "GET /api/me/tenants": [tenants(U), tenants(U, T)],
      "POST /api/invitations/abc/accept": { status: 200, body: { tenant_id: T } },
    });
    renderAt("/invitations/accept#token=abc");
    const accept = await screen.findByRole("button", { name: "Accept invitation" });
    expect(api.sent("POST /api/invitations/abc/accept")).toHaveLength(0);
    fireEvent.click(accept);
    expect(await screen.findByText(`at /team from - tenant ${T}`)).toBeTruthy();
    expect(api.sent("POST /api/invitations/abc/accept")).toHaveLength(1);
  });

  it("gives one answer for every refusal", async () => {
    mockApi({
      "GET /api/me/tenants": tenants(U),
      "POST /api/invitations/used/accept": apiError(404, "NOT_FOUND"),
    });
    renderAt("/invitations/accept#token=used");
    fireEvent.click(await screen.findByRole("button", { name: "Accept invitation" }));
    expect(await screen.findByRole("heading", { name: "Invitation not accepted" })).toBeTruthy();
  });

  it("refuses a link with no token without calling the API", async () => {
    const api = mockApi({ "GET /api/me/tenants": tenants(U) });
    renderAt("/invitations/accept");
    expect(await screen.findByRole("heading", { name: "Invitation not accepted" })).toBeTruthy();
    await waitFor(() =>
      expect(api.calls.some((call) => call.key.includes("/accept"))).toBe(false),
    );
  });
});
