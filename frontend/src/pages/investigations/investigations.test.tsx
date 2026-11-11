// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PermissionsProvider } from "../../permissions/PermissionsContext";
import { RequirePermission } from "../../permissions/RequirePermission";
import { SessionProvider } from "../../session/SessionContext";
import { apiError, mockApi, type Reply } from "../../test/api";
import { stubLocalStorage } from "../../test/storage";
import type { DomainCase } from "../operations/types";
import { Investigations } from "./Investigations";
import type { Investigation, InvestigationSummary } from "./types";

const T = "a0000000-0000-4000-8000-000000000000";
const CASE: DomainCase = {
  id: "c0000000-0000-4000-8000-000000000001",
  run_id: "r-1",
  case_type: "DUPLICATE_CAPTURE",
  reconciliation_scope: "PROVIDER_TO_MERCHANT",
  severity: "high",
  status: "HUMAN_REVIEW",
  expected_amount: "25.000000",
  observed_amount: "50.000000",
  currency: "USD",
  assignee_id: null,
  reason: null,
  evidence_provenance: ["provider_event"],
  created_at: "2026-11-11T10:00:00Z",
};
const RESOLVED_CASE: DomainCase = {
  ...CASE,
  id: "c0000000-0000-4000-8000-000000000002",
  status: "RESOLVED",
};
const INV = "i0000000-0000-4000-8000-000000000001";
const EVIDENCE = "e0000000-0000-4000-8000-000000000001";
const SUMMARY: InvestigationSummary = {
  investigation_id: INV,
  case_id: CASE.id,
  status: "SUCCEEDED",
  proposed_classification: "DUPLICATE_CAPTURE",
  recommended_playbook: "finops:PB-002",
  confidence: "high",
  error: null,
  created_at: "2026-11-11T10:01:00Z",
  finished_at: "2026-11-11T10:01:20Z",
};
const RUNNING: Investigation = {
  ...SUMMARY,
  status: "RUNNING",
  proposed_classification: null,
  recommended_playbook: null,
  confidence: null,
  finished_at: null,
  model: null,
  prompt_version: null,
  tool_registry_version: "tools-1",
  result: null,
  tool_calls: [],
};
const DONE: Investigation = {
  ...RUNNING,
  ...SUMMARY,
  model: "gpt-test",
  prompt_version: "inv-1",
  result: {
    case_id: CASE.id,
    classification: "DUPLICATE_CAPTURE",
    root_cause_summary: "The buyer was captured twice for one order.",
    evidence: [
      {
        evidence_id: EVIDENCE,
        source_id: "s0000000-0000-4000-8000-000000000001",
        record_id: "sr000000-0000-4000-8000-000000000001",
        fact: "Two COMPLETED captures reference ORD-7.",
      },
    ],
    recommended_playbook: "finops:PB-002",
    requires_approval: true,
    confidence: "high",
    missing_evidence: ["The buyer's dispute history"],
  },
  tool_calls: [
    {
      id: "tc-1",
      tool: "get_case",
      tool_version: "1.0.0",
      permission: "case.read",
      input: { case_id: CASE.id },
      output: { case_type: "DUPLICATE_CAPTURE" },
      output_hash: "h1",
      evidence_ids: [],
      outcome: "OK",
      error: null,
      created_at: "2026-11-11T10:01:01Z",
    },
    {
      id: "tc-2",
      tool: "get_source_evidence",
      tool_version: "1.0.0",
      permission: "evidence.read",
      input: { record_id: "sr-1" },
      output: { captures: ["CAP-A", "CAP-B"] },
      output_hash: "h2",
      evidence_ids: [EVIDENCE],
      outcome: "OK",
      error: null,
      created_at: "2026-11-11T10:01:02Z",
    },
  ],
};

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

function renderInvestigations(keys: string[], extra: Record<string, Reply | Reply[]> = {}) {
  const api = mockApi({
    "GET /api/me/tenants": { status: 200, body: { tenants: [{ id: T, name: "Acme" }] } },
    [`GET /api/me/permissions?tenant_id=${T}`]: granted(keys),
    "GET /api/investigations": { status: 200, body: [SUMMARY] },
    "GET /api/domain-cases": { status: 200, body: [CASE, RESOLVED_CASE] },
    [`GET /api/investigations/${INV}`]: { status: 200, body: DONE },
    ...extra,
  });
  render(
    <MemoryRouter>
      <SessionProvider>
        <PermissionsProvider>
          <RequirePermission permission="investigation.read">
            <Investigations pollMs={5} />
          </RequirePermission>
        </PermissionsProvider>
      </SessionProvider>
    </MemoryRouter>,
  );
  return api;
}

describe("Investigations", () => {
  it("lists investigations and offers no start without case.investigate", async () => {
    const api = renderInvestigations(["investigation.read"]);
    expect(await screen.findByRole("cell", { name: "finops:PB-002" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Start investigation" })).toBeNull();
    expect(api.calls.map((call) => call.key)).not.toContain("GET /api/domain-cases");
  });

  it("starts on an open case, polls while RUNNING and shows the validated result", async () => {
    const api = renderInvestigations(["investigation.read", "case.read", "case.investigate"], {
      [`POST /api/agent/investigate/${CASE.id}`]: {
        status: 202,
        body: { investigation_id: INV, status: "RUNNING" },
      },
      [`GET /api/investigations/${INV}`]: [
        { status: 200, body: RUNNING },
        { status: 200, body: RUNNING },
        { status: 200, body: DONE },
      ],
    });
    const select = await screen.findByRole("combobox", { name: "Case" });
    // The placeholder and the open case; a resolved case is not offered.
    await waitFor(() => expect(within(select).getAllByRole("option")).toHaveLength(2));
    fireEvent.change(select, { target: { value: CASE.id } });
    fireEvent.click(screen.getByRole("button", { name: "Start investigation" }));

    expect(await screen.findByText(/still working/)).toBeTruthy();
    const detail = await screen.findByRole("region", { name: "Investigation detail" });
    expect(await within(detail).findByText("The buyer was captured twice for one order."))
      .toBeTruthy();
    expect(within(detail).getByText("PROPOSED")).toBeTruthy();
    expect(within(detail).getByText("The buyer's dispute history")).toBeTruthy();
    expect(within(detail).getByText("Required")).toBeTruthy();
    expect(screen.queryByText(/still working/)).toBeNull();
    const reads = api.calls.filter((call) => call.key === `GET /api/investigations/${INV}`);
    expect(reads.length).toBeGreaterThanOrEqual(3);

    // Nothing on this screen creates a request.
    expect(api.calls.some((call) => call.key.startsWith("POST /api/playbook"))).toBe(false);
    expect(screen.queryByRole("button", { name: /request/i })).toBeNull();
  });

  it("opens cited evidence at the trace call that returned it", async () => {
    renderInvestigations(["investigation.read"]);
    fireEvent.click(await screen.findByRole("button", { name: `Open investigation ${INV}` }));
    fireEvent.click(await screen.findByRole("button", { name: `Open evidence ${EVIDENCE}` }));
    const output = await screen.findByLabelText("get_source_evidence output");
    expect(output.textContent).toContain("CAP-B");
    expect(screen.queryByLabelText("get_case output")).toBeNull();
  });

  it("shows a failed investigation's recorded reason", async () => {
    renderInvestigations(["investigation.read"], {
      [`GET /api/investigations/${INV}`]: {
        status: 200,
        body: { ...RUNNING, status: "FAILED", error: "RESPONSE_REFUSED" },
      },
    });
    fireEvent.click(await screen.findByRole("button", { name: `Open investigation ${INV}` }));
    expect((await screen.findByRole("alert")).textContent).toContain("RESPONSE_REFUSED");
  });

  it("shows the envelope message when a start is refused", async () => {
    renderInvestigations(["investigation.read", "case.read", "case.investigate"], {
      [`POST /api/agent/investigate/${CASE.id}`]: apiError(
        409,
        "INVESTIGATION_RUNNING",
        "This case already has a running investigation.",
      ),
    });
    const select = await screen.findByRole("combobox", { name: "Case" });
    await waitFor(() => expect(within(select).getAllByRole("option")).toHaveLength(2));
    fireEvent.change(select, { target: { value: CASE.id } });
    fireEvent.click(screen.getByRole("button", { name: "Start investigation" }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      "This case already has a running investigation.",
    );
  });
});
