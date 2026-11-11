// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PermissionsProvider } from "../../permissions/PermissionsContext";
import { RequirePermission } from "../../permissions/RequirePermission";
import { SessionProvider } from "../../session/SessionContext";
import { apiError, mockApi, type Reply } from "../../test/api";
import { stubLocalStorage } from "../../test/storage";
import { PlaybookRequests } from "./PlaybookRequests";
import type { Execution, PlaybookRequest, Readiness } from "./types";

const T = "a0000000-0000-4000-8000-000000000000";
const R = "b0000000-0000-4000-8000-000000000001";
const X = "d0000000-0000-4000-8000-000000000001";
const AUDIT = "f0000000-0000-4000-8000-000000000001";
const PROOF = {
  order_reference: "ORD-7",
  original: { capture_id: "CAP-A", captured_at: "2026-11-11T09:00:00Z" },
  duplicate: { capture_id: "CAP-B", captured_at: "2026-11-11T09:00:05Z" },
  rule:
    "two distinct PayPal captures, both COMPLETED, for one merchant order; " +
    "the later is the duplicate; no refund or dispute exists",
};
const REQUEST: PlaybookRequest = {
  id: R,
  case_id: "c0000000-0000-4000-8000-000000000001",
  playbook_id: "finops:PB-002",
  playbook_version: "1.0.0",
  pack_version: "0.1.0",
  connector_id: "paypal",
  action_capability: "refunds.create",
  requested_by: "u0000000-0000-4000-8000-000000000009",
  parameters: {},
  target: { paypal_source_id: "s-1", capture_id: "CAP-B", amount: "25.00", currency: "USD" },
  status: "NEEDS_APPROVAL",
  created_at: "2026-11-11T10:00:00Z",
  updated_at: "2026-11-11T10:00:00Z",
  decisions: [
    {
      id: "pd-1",
      stage: "REQUEST",
      parent_decision_id: null,
      policy_version: "policy-1",
      approval_required: true,
      decision: "NEEDS_APPROVAL",
      conditions: { proof: PROOF },
      reason: "PB-002 always requires approval.",
      attestation_id: null,
      created_at: "2026-11-11T10:00:00Z",
    },
  ],
  approval: null,
  execution: null,
  requested_by_me: false,
  audit_refs: [
    {
      id: AUDIT,
      action: "playbook.requested",
      decision: null,
      created_at: "2026-11-11T10:00:00Z",
    },
  ],
};
const APPROVED: PlaybookRequest = {
  ...REQUEST,
  status: "EXECUTION_CREATED",
  decisions: [
    ...REQUEST.decisions,
    {
      ...REQUEST.decisions[0],
      id: "pd-2",
      stage: "EXECUTION",
      parent_decision_id: "pd-1",
      decision: "ALLOW",
      conditions: {},
      reason: "Preconditions, target, connector action, mode and release gate hold.",
      attestation_id: "at-1",
    },
  ],
  approval: {
    id: "ap-1",
    approver_user_id: "u0000000-0000-4000-8000-000000000002",
    decision: "APPROVED",
    reason: null,
    created_at: "2026-11-11T10:05:00Z",
  },
  execution: { id: X, status: "GRANT_ISSUED" },
};
const EXECUTION: Execution = {
  id: X,
  request_id: R,
  status: "EXECUTING",
  playbook_id: "finops:PB-002",
  connector_id: "paypal",
  action_capability: "refunds.create",
  target: REQUEST.target,
  idempotency_key: "exec-key-1",
  reason: null,
  result_ref: null,
  started_at: "2026-11-11T10:05:01Z",
  completed_at: null,
  attempts: [
    {
      sequence: 1,
      connector_id: "paypal",
      action_capability: "refunds.create",
      request_evidence: {},
      response_evidence: null,
      status: "STARTED",
      error_class: null,
      started_at: "2026-11-11T10:05:01Z",
      finished_at: null,
    },
  ],
};
const VERIFIED: Execution = {
  ...EXECUTION,
  status: "SUCCEEDED",
  completed_at: "2026-11-11T10:05:04Z",
  result_ref: {
    readback: "refunds.read",
    evidence: { refund: { id: "RF-1" } },
    evidence_hash: "9f8e",
  },
  attempts: [
    ...EXECUTION.attempts,
    { ...EXECUTION.attempts[0], sequence: 2, status: "SUCCEEDED" },
    {
      ...EXECUTION.attempts[0],
      sequence: 3,
      action_capability: "refunds.read",
      status: "VERIFIED",
    },
  ],
};
const SHADOW: Readiness = {
  domain_pack_id: "finops",
  connector_id: "paypal",
  action_capability: "refunds.create",
  mode: "SHADOW",
  release_gate: { passes: false, failing_check: "mode" },
};

type Grant = string | { key: string; scopes?: string[]; denied?: string[] };

const granted = (grants: Grant[]): Reply => ({
  status: 200,
  body: {
    permissions: grants.map((grant) =>
      typeof grant === "string"
        ? { key: grant, tenant_wide: true, scopes: [], denied_scopes: [] }
        : {
            key: grant.key,
            tenant_wide: false,
            scopes: (grant.scopes ?? []).map((id) => ({ type: "playbook", id })),
            denied_scopes: (grant.denied ?? []).map((id) => ({ type: "playbook", id })),
          },
    ),
  },
});

beforeEach(() => {
  stubLocalStorage();
});
afterEach(() => vi.unstubAllGlobals());

function renderPlaybooks(grants: Grant[], extra: Record<string, Reply | Reply[]> = {}) {
  const api = mockApi({
    "GET /api/me/tenants": { status: 200, body: { tenants: [{ id: T, name: "Acme" }] } },
    [`GET /api/me/permissions?tenant_id=${T}`]: granted(grants),
    "GET /api/playbook-requests": { status: 200, body: [REQUEST] },
    [`GET /api/playbook-requests/${R}`]: { status: 200, body: REQUEST },
    [`GET /api/tenants/${T}/execution-readiness`]: { status: 200, body: [SHADOW] },
    ...extra,
  });
  render(
    <MemoryRouter initialEntries={["/playbooks"]}>
      <SessionProvider>
        <PermissionsProvider>
          <RequirePermission permission="playbook.read">
            <PlaybookRequests pollMs={5} />
          </RequirePermission>
        </PermissionsProvider>
      </SessionProvider>
    </MemoryRouter>,
  );
  return api;
}

async function openRequest() {
  fireEvent.click(await screen.findByRole("button", { name: `Open request ${R}` }));
  return screen.findByRole("region", { name: "Request detail" });
}

describe("PlaybookRequests", () => {
  it("shows the request, its decisions, proof and audit to a reader with no actions", async () => {
    const api = renderPlaybooks(["playbook.read"]);
    const detail = await openRequest();
    expect(await within(detail).findByText("PB-002 always requires approval.")).toBeTruthy();
    expect(within(detail).getByRole("region", { name: "Duplicate capture proof" })).toBeTruthy();
    expect(within(detail).getByText("Waiting for an independent approver.")).toBeTruthy();
    expect(within(detail).getByText(/playbook\.requested/)).toBeTruthy();
    expect(within(detail).queryByRole("button", { name: "Approve" })).toBeNull();
    expect(within(detail).queryByRole("button", { name: /Open audit/ })).toBeNull();
    // The mode needs execution.read; without it nothing is fetched and nothing is claimed.
    expect(api.calls.map((call) => call.key)).not.toContain(
      `GET /api/tenants/${T}/execution-readiness`,
    );
    expect(within(detail).getByText("Mode not visible")).toBeTruthy();
    expect(within(detail).getByText("PayPal Sandbox")).toBeTruthy();
  });

  it("shows the PB-002 duplicate proof an approver decides on", async () => {
    renderPlaybooks(["playbook.read", "playbook.approve"]);
    const detail = await openRequest();
    const proof = await within(detail).findByRole("region", { name: "Duplicate capture proof" });
    const rows = within(proof).getAllByRole("row");
    expect(rows[1].textContent).toContain("Original");
    expect(rows[1].textContent).toContain("CAP-A");
    expect(rows[2].textContent).toContain("Duplicate");
    expect(rows[2].textContent).toContain("CAP-B");
    expect(within(proof).getByText("ORD-7")).toBeTruthy();
    expect(within(proof).getByText("25.00 USD")).toBeTruthy();
    expect(within(proof).getByText(/no refund or dispute exists/)).toBeTruthy();
    expect(within(proof).queryByRole("alert")).toBeNull();
    expect(within(detail).getByRole("button", { name: "Approve" })).toBeTruthy();
  });

  it("warns when the refund target is not the duplicate capture", async () => {
    renderPlaybooks(["playbook.read"], {
      [`GET /api/playbook-requests/${R}`]: {
        status: 200,
        body: { ...REQUEST, target: { ...REQUEST.target, capture_id: "CAP-A" } },
      },
    });
    const detail = await openRequest();
    const proof = await within(detail).findByRole("region", { name: "Duplicate capture proof" });
    expect(within(proof).getByRole("alert").textContent).toContain("Do not approve");
  });

  it("shows the exact scope's mode beside the PayPal Sandbox environment", async () => {
    renderPlaybooks(["playbook.read", "execution.read"]);
    const detail = await openRequest();
    const mode = await within(detail).findByText("SHADOW");
    expect(mode.closest("p")?.textContent).toContain("PayPal Sandbox");
  });

  it("hides approve and reject from the requester (POL-01)", async () => {
    renderPlaybooks(["playbook.read", "playbook.approve", "playbook.reject"], {
      [`GET /api/playbook-requests/${R}`]: {
        status: 200,
        body: { ...REQUEST, requested_by_me: true },
      },
    });
    const detail = await openRequest();
    expect(await within(detail).findByText(/another member must approve it/)).toBeTruthy();
    expect(within(detail).queryByRole("button", { name: "Approve" })).toBeNull();
    expect(within(detail).queryByRole("button", { name: "Reject" })).toBeNull();
  });

  it("gates approve on the playbook's own scope and honours a denied playbook", async () => {
    renderPlaybooks([
      "playbook.read",
      { key: "playbook.approve", scopes: ["finops:PB-001"] },
      { key: "playbook.reject", denied: ["finops:PB-002"] },
    ]);
    const detail = await openRequest();
    expect(await within(detail).findByText("Waiting for an independent approver.")).toBeTruthy();
    expect(within(detail).queryByRole("button", { name: "Approve" })).toBeNull();
    expect(within(detail).queryByRole("button", { name: "Reject" })).toBeNull();
  });

  it("approves, then follows the execution to a verified readback", async () => {
    const api = renderPlaybooks(
      ["playbook.read", { key: "playbook.approve", scopes: ["finops:PB-002"] }, "execution.read"],
      {
        [`GET /api/playbook-requests/${R}`]: [
          { status: 200, body: REQUEST },
          { status: 200, body: APPROVED },
        ],
        [`POST /api/playbook-requests/${R}/approve`]: { status: 200, body: APPROVED },
        [`GET /api/executions/${X}`]: [
          { status: 200, body: EXECUTION },
          { status: 200, body: VERIFIED },
        ],
      },
    );
    const detail = await openRequest();
    fireEvent.click(await within(detail).findByRole("button", { name: "Approve" }));
    const execution = await within(detail).findByRole("region", { name: "Execution" });
    expect(await within(execution).findByText(/Verified by refunds\.read/)).toBeTruthy();
    expect(within(execution).getByText("VERIFIED")).toBeTruthy();
    expect(within(execution).getByLabelText("Readback evidence").textContent).toContain("RF-1");
    expect(api.sent(`POST /api/playbook-requests/${R}/approve`)).toEqual([{}]);
    const reads = api.calls.filter((call) => call.key === `GET /api/executions/${X}`);
    expect(reads.length).toBeGreaterThanOrEqual(2);
  });

  it("needs a reason to reject and shows a refusal's envelope message", async () => {
    const api = renderPlaybooks(["playbook.read", "playbook.reject"], {
      [`POST /api/playbook-requests/${R}/reject`]: apiError(
        403,
        "MISSING_PERMISSION",
        "Permission required.",
      ),
    });
    const detail = await openRequest();
    fireEvent.click(await within(detail).findByRole("button", { name: "Reject" }));
    expect((await within(detail).findByRole("alert")).textContent).toBe(
      "Give a reason to reject.",
    );
    expect(api.sent(`POST /api/playbook-requests/${R}/reject`)).toEqual([]);
    fireEvent.change(within(detail).getByLabelText(/Reason/), {
      target: { value: "The original capture is disputed." },
    });
    fireEvent.click(within(detail).getByRole("button", { name: "Reject" }));
    expect((await within(detail).findByRole("alert")).textContent).toBe("Permission required.");
    expect(api.sent(`POST /api/playbook-requests/${R}/reject`)).toEqual([
      { reason: "The original capture is disputed." },
    ]);
  });

  it("explains an execution-stage POLICY_BLOCKED decision", async () => {
    renderPlaybooks(["playbook.read"], {
      [`GET /api/playbook-requests/${R}`]: {
        status: 200,
        body: {
          ...APPROVED,
          status: "PRECONDITIONS_VALIDATED",
          execution: null,
          decisions: [
            APPROVED.decisions[0],
            { ...APPROVED.decisions[1], decision: "POLICY_BLOCKED", attestation_id: null },
          ],
        },
      },
    });
    const detail = await openRequest();
    expect(await within(detail).findByText(/nothing was sent/)).toBeTruthy();
  });

  it("opens an audit record for a holder of audit.read", async () => {
    renderPlaybooks(["playbook.read", "audit.read"], {
      [`GET /api/audit/${AUDIT}`]: {
        status: 200,
        body: {
          id: AUDIT,
          action: "playbook.requested",
          permission_key: "playbook.request",
          decision: null,
          detail: { request_id: R },
          created_at: "2026-11-11T10:00:00Z",
        },
      },
    });
    const detail = await openRequest();
    fireEvent.click(
      await within(detail).findByRole("button", {
        name: `Open audit playbook.requested ${AUDIT}`,
      }),
    );
    await waitFor(() =>
      expect(within(detail).getByLabelText("Audit record").textContent).toContain(
        "playbook.request",
      ),
    );
  });
});
