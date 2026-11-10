// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PermissionsProvider } from "../../permissions/PermissionsContext";
import { RequirePermission } from "../../permissions/RequirePermission";
import { SessionProvider } from "../../session/SessionContext";
import { apiError, mockApi, type Reply } from "../../test/api";
import { stubLocalStorage } from "../../test/storage";
import { TeamManagement } from "./TeamManagement";
import type { Member, PermissionSet } from "./types";

const T = "a0000000-0000-4000-8000-000000000000";
const BASE = `/api/tenants/${T}`;
const OWNER: Member = {
  user_id: "u-owner",
  email: "owner@example.com",
  status: "ACTIVE",
  joined_at: null,
  owns_tenant: true,
  grants: [],
};
const PERSON: Member = {
  user_id: "u-person",
  email: "person@example.com",
  status: "ACTIVE",
  joined_at: null,
  owns_tenant: false,
  grants: [
    {
      id: "g-1",
      permission_set_key: "reviewer-set",
      display_name: "Reviewer",
      scope_type: null,
      scope_id: null,
    },
  ],
};
// Made-up set keys: the role-name ban forbids real ones anywhere in frontend/src.
const SETS: PermissionSet[] = [
  { key: "reviewer-set", display_name: "Reviewer", principal_type: "human", derivation: null },
  { key: "derived-set", display_name: "Derived", principal_type: "human", derivation: "OWNER_POINTER" },
  { key: "service-set", display_name: "Service", principal_type: "agent", derivation: null },
];
const ALL_KEYS = [
  "member.read",
  "member.invite",
  "member.invitation.manage",
  "member.role.assign",
  "member.remove",
  "tenant.ownership.transfer",
  "role.read",
];

const granted = (keys: string[]): Reply => ({
  status: 200,
  body: {
    permissions: keys.map((key) => ({ key, tenant_wide: true, scopes: [], denied_scopes: [] })),
  },
});

beforeEach(() => {
  stubLocalStorage();
});
afterEach(() => vi.unstubAllGlobals());

function renderTeam(keys: string[], extra: Record<string, Reply | Reply[]> = {}) {
  const api = mockApi({
    "GET /api/me/tenants": { status: 200, body: { tenants: [{ id: T, name: "Acme" }] } },
    [`GET /api/me/permissions?tenant_id=${T}`]: granted(keys),
    [`GET ${BASE}/members`]: { status: 200, body: [OWNER, PERSON] },
    [`GET ${BASE}/invitations`]: { status: 200, body: [] },
    "GET /api/permission-sets": { status: 200, body: SETS },
    ...extra,
  });
  render(
    <MemoryRouter>
      <SessionProvider>
        <PermissionsProvider>
          <RequirePermission permission="member.read">
            <TeamManagement />
          </RequirePermission>
        </PermissionsProvider>
      </SessionProvider>
    </MemoryRouter>,
  );
  return api;
}

// The ownership picker lists emails too, so find the one inside the members table.
const row = (email: string) =>
  screen.getAllByText(email).map((element) => element.closest("tr")).find(Boolean) as HTMLElement;

describe("TeamManagement", () => {
  it("shows a read-only team to a holder of member.read alone", async () => {
    const api = renderTeam(["member.read"]);
    expect(await screen.findByText("person@example.com")).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Invite a member" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "Invitations" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "Transfer ownership" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Remove" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Revoke Reviewer/ })).toBeNull();
    const fetched = api.calls.map((call) => call.key);
    expect(fetched).not.toContain(`GET ${BASE}/invitations`);
    expect(fetched).not.toContain("GET /api/permission-sets");
  });

  it("offers every control to a holder of every key", async () => {
    renderTeam(ALL_KEYS);
    expect(await screen.findByRole("heading", { name: "Invite a member" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Invitations" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Transfer ownership" })).toBeTruthy();
    expect(within(row("person@example.com")).getByRole("button", { name: "Remove" })).toBeTruthy();
    // The owner cannot be removed or suspended, so no control is offered.
    expect(within(row("owner@example.com")).queryByRole("button", { name: "Remove" })).toBeNull();
  });

  it("offers only sets a person can be granted", async () => {
    renderTeam(ALL_KEYS);
    const form = (await screen.findByRole("heading", { name: "Invite a member" }))
      .parentElement as HTMLElement;
    const options = within(form).getAllByRole("option").map((option) => option.textContent);
    expect(options).toEqual(["Choose a set", "Reviewer"]);
  });

  it("invites with the chosen set and reports a failed delivery", async () => {
    const api = renderTeam(ALL_KEYS, {
      [`POST ${BASE}/invitations`]: { status: 201, body: { delivery_status: "FAILED" } },
    });
    const form = (await screen.findByRole("heading", { name: "Invite a member" }))
      .parentElement as HTMLElement;
    fireEvent.change(within(form).getByLabelText("Email"), {
      target: { value: "new@example.com" },
    });
    fireEvent.change(within(form).getByLabelText("Permission set"), {
      target: { value: "reviewer-set" },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Send invitation" }));
    expect((await screen.findByRole("status")).textContent).toMatch(/was not delivered/);
    expect(api.sent(`POST ${BASE}/invitations`)).toEqual([
      { email: "new@example.com", grants: [{ permission_set_key: "reviewer-set" }] },
    ]);
  });

  it("refuses a half-filled scope instead of granting tenant-wide", async () => {
    renderTeam(ALL_KEYS);
    const form = (await screen.findByRole("heading", { name: "Invite a member" }))
      .parentElement as HTMLElement;
    fireEvent.change(within(form).getByLabelText("Email"), { target: { value: "x@example.com" } });
    fireEvent.change(within(form).getByLabelText("Permission set"), {
      target: { value: "reviewer-set" },
    });
    fireEvent.change(within(form).getByLabelText(/Scope type/), { target: { value: "source" } });
    const send = within(form).getByRole("button", { name: "Send invitation" }) as HTMLButtonElement;
    expect(send.disabled).toBe(true);
  });

  it("removes a member only after confirming", async () => {
    const api = renderTeam(ALL_KEYS, {
      [`DELETE ${BASE}/members/u-person`]: { status: 200, body: { status: "REMOVED" } },
    });
    await screen.findAllByText("person@example.com");
    fireEvent.click(within(row("person@example.com")).getByRole("button", { name: "Remove" }));
    expect(api.sent(`DELETE ${BASE}/members/u-person`)).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Confirm remove" }));
    expect((await screen.findByRole("status")).textContent).toBe("person@example.com was removed.");
    expect(api.sent(`DELETE ${BASE}/members/u-person`)).toHaveLength(1);
  });

  it("suspends through its own route", async () => {
    const api = renderTeam(ALL_KEYS, {
      [`POST ${BASE}/members/u-person/suspend`]: { status: 200, body: { status: "SUSPENDED" } },
    });
    await screen.findAllByText("person@example.com");
    fireEvent.click(within(row("person@example.com")).getByRole("button", { name: "Suspend" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm suspend" }));
    await waitFor(() => expect(api.sent(`POST ${BASE}/members/u-person/suspend`)).toHaveLength(1));
  });

  it("revokes one grant by its ID", async () => {
    const api = renderTeam(ALL_KEYS, {
      [`PATCH ${BASE}/members/u-person/grants`]: { status: 200, body: { status: "UPDATED" } },
    });
    fireEvent.click(
      await screen.findByRole("button", { name: "Revoke Reviewer from person@example.com" }),
    );
    await waitFor(() =>
      expect(api.sent(`PATCH ${BASE}/members/u-person/grants`)).toEqual([{ revoke: ["g-1"] }]),
    );
  });

  it("transfers ownership after confirming, then re-reads permissions", async () => {
    const api = renderTeam(ALL_KEYS, {
      [`POST ${BASE}/ownership-transfer`]: { status: 200, body: {} },
    });
    const section = (await screen.findByRole("heading", { name: "Transfer ownership" }))
      .parentElement as HTMLElement;
    fireEvent.change(within(section).getByLabelText("New owner"), {
      target: { value: "u-person" },
    });
    fireEvent.click(within(section).getByRole("button", { name: "Transfer ownership" }));
    fireEvent.click(within(section).getByRole("button", { name: "Confirm transfer" }));
    await waitFor(() =>
      expect(api.sent(`POST ${BASE}/ownership-transfer`)).toEqual([
        { new_owner_user_id: "u-person" },
      ]),
    );
    const permissionReads = () =>
      api.calls.filter((call) => call.key.startsWith("GET /api/me/permissions")).length;
    await waitFor(() => expect(permissionReads()).toBeGreaterThan(1));
  });

  it("shows the API's message when a change is refused", async () => {
    renderTeam(ALL_KEYS, {
      [`DELETE ${BASE}/members/u-person`]: apiError(
        403,
        "POLICY_DENIED",
        "This action is not allowed by policy.",
      ),
    });
    await screen.findAllByText("person@example.com");
    fireEvent.click(within(row("person@example.com")).getByRole("button", { name: "Remove" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm remove" }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      "This action is not allowed by policy.",
    );
  });
});
