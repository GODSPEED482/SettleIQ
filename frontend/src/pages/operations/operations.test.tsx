// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PermissionsProvider } from "../../permissions/PermissionsContext";
import { RequirePermission } from "../../permissions/RequirePermission";
import { SessionProvider } from "../../session/SessionContext";
import { apiError, mockApi, type Reply } from "../../test/api";
import { stubLocalStorage } from "../../test/storage";
import { DomainOperations } from "./DomainOperations";
import type {
  DomainCase,
  DomainRun,
  EntityDetail,
  EntitySummary,
  ResolutionDecision,
} from "./types";

const T = "a0000000-0000-4000-8000-000000000000";
const RUN: DomainRun = {
  id: "r0000000-0000-4000-8000-000000000001",
  pack_namespace: "finops",
  pack_version: "0.1.0",
  ontology_id: "o-1",
  ontology_version: 3,
  ontology_content_hash: "abcdef0123456789",
  rule_version: "finops-rules-1",
  inputs_hash: "fedcba9876543210",
  status: "SUCCEEDED",
  findings: [{ case_type: "SETTLEMENT_VARIANCE", scope: "INTER_ENTITY", case_id: "c-1", new_case: true }],
  failure: null,
  started_at: "2026-11-11T10:00:00Z",
  finished_at: "2026-11-11T10:00:01Z",
};
const caseOf = (overrides: Partial<DomainCase>): DomainCase => ({
  id: "c-1",
  run_id: RUN.id,
  case_type: "SETTLEMENT_VARIANCE",
  reconciliation_scope: "INTER_ENTITY",
  severity: "high",
  status: "OPEN",
  expected_amount: "125.630000",
  observed_amount: "124.630000",
  currency: "USD",
  assignee_id: null,
  reason: null,
  evidence_provenance: ["synthetic_reference"],
  created_at: "2026-11-11T10:00:01Z",
  ...overrides,
});
const ENTITY: EntitySummary = { id: "e0000000-1", class_iri: "finops:Capture", canonical_key: "CAP-1" };
const DETAIL: EntityDetail = {
  ...ENTITY,
  observations: [
    {
      id: "ob000000-1",
      property_iri: "finops:lifecycleStatus",
      source_id: "s0000000-1",
      field_path: "$.status",
      raw_value: "COMPLETED",
      normalized_value: "COMPLETED",
      currency: null,
      value_basis: "OBSERVED",
      provenance: "synthetic_reference",
      observed_at: "2026-11-11T09:00:00Z",
    },
    {
      id: "ob000000-2",
      property_iri: "finops:lifecycleStatus",
      source_id: "s0000000-2",
      field_path: "$.state",
      raw_value: "pending",
      normalized_value: "PENDING",
      currency: null,
      value_basis: "OBSERVED",
      provenance: "database",
      observed_at: "2026-11-11T09:00:00Z",
    },
  ],
  resolved: [
    {
      run_id: RUN.id,
      property_iri: "finops:amount",
      status: "RESOLVED",
      value: "100.000000",
      currency: "USD",
      contributing_observation_ids: ["ob000000-3"],
    },
    {
      run_id: RUN.id,
      property_iri: "finops:lifecycleStatus",
      status: "CONFLICT",
      value: null,
      currency: null,
      contributing_observation_ids: ["ob000000-1", "ob000000-2"],
    },
    {
      run_id: RUN.id,
      property_iri: "finops:orderReference",
      status: "UNRESOLVED",
      value: null,
      currency: null,
      contributing_observation_ids: [],
    },
  ],
};
const DECISION: ResolutionDecision = {
  id: "d0000000-1",
  source_record_id: "sr000000-1",
  class_iri: "finops:Order",
  features: { orderReference: "ORD-5", providerReference: "PO-6" },
  candidate_entity_ids: ["e-ord-5", "e-ord-6"],
  status: "PENDING_REVIEW",
  created_at: "2026-11-11T10:00:01Z",
};
const READ_KEYS = ["domain_run.read", "case.read", "entity.read", "entity_resolution.read"];
const WRITE_KEYS = ["domain_run.create", "case.triage", "entity_resolution.review"];

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

function renderOperations(keys: string[], extra: Record<string, Reply | Reply[]> = {}) {
  const api = mockApi({
    "GET /api/me/tenants": { status: 200, body: { tenants: [{ id: T, name: "Acme" }] } },
    [`GET /api/me/permissions?tenant_id=${T}`]: granted(keys),
    "GET /api/domain-runs": { status: 200, body: [RUN] },
    "GET /api/domain-cases": { status: 200, body: [caseOf({})] },
    "GET /api/entities": { status: 200, body: [ENTITY] },
    [`GET /api/entities/${ENTITY.id}`]: { status: 200, body: DETAIL },
    "GET /api/entity-resolution": { status: 200, body: [DECISION] },
    [`GET /api/tenants/${T}/members`]: { status: 200, body: [] },
    ...extra,
  });
  render(
    <MemoryRouter>
      <SessionProvider>
        <PermissionsProvider>
          <RequirePermission permission="domain_run.read">
            <DomainOperations />
          </RequirePermission>
        </PermissionsProvider>
      </SessionProvider>
    </MemoryRouter>,
  );
  return api;
}

const section = (name: string) =>
  screen.getByRole("heading", { name }).closest("section") as HTMLElement;

describe("DomainOperations", () => {
  it("shows runs with their pinned versions and fetches nothing else without read keys", async () => {
    const api = renderOperations(["domain_run.read"]);
    expect(await screen.findByText("finops@0.1.0")).toBeTruthy();
    expect(screen.getByText("abcdef012345")).toBeTruthy();
    expect(screen.getByText("finops-rules-1")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Start run" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "Cases" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "Entities" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "Identity review" })).toBeNull();
    const fetched = api.calls.map((call) => call.key);
    expect(fetched).not.toContain("GET /api/domain-cases");
    expect(fetched).not.toContain("GET /api/entities");
    expect(fetched).not.toContain("GET /api/entity-resolution");
  });

  it("hides every action from a holder of the read keys alone", async () => {
    renderOperations(READ_KEYS);
    expect(await screen.findByRole("cell", { name: "SETTLEMENT_VARIANCE" })).toBeTruthy();
    expect(await screen.findAllByText(/^Candidate/, { selector: "li" })).toHaveLength(2);
    expect(screen.queryByRole("button", { name: "Start run" })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Triage / })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Accept / })).toBeNull();
    expect(screen.queryByRole("button", { name: "Reject, leave unlinked" })).toBeNull();
  });

  it("offers each action to a holder of its key", async () => {
    renderOperations([...READ_KEYS, ...WRITE_KEYS]);
    expect(await screen.findByRole("button", { name: "Start run" })).toBeTruthy();
    expect(await screen.findByRole("button", { name: /^Triage / })).toBeTruthy();
    expect(await screen.findAllByRole("button", { name: /^Accept / })).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Reject, leave unlinked" })).toBeTruthy();
  });

  it("labels synthetic evidence on cases and observations", async () => {
    renderOperations(READ_KEYS);
    const cases = await waitFor(() => section("Cases"));
    expect(await within(cases).findByText("SYNTHETIC/REFERENCE")).toBeTruthy();
    expect(within(cases).getByText("INTER_ENTITY")).toBeTruthy();
    expect(within(cases).getByText("125.630000 USD")).toBeTruthy();

    fireEvent.click(await screen.findByRole("button", { name: "finops:Capture CAP-1" }));
    const observations = await screen.findByRole("table", { name: "Observations" });
    const rows = within(observations).getAllByRole("row");
    expect(within(rows[1]).getByText("SYNTHETIC/REFERENCE")).toBeTruthy();
    expect(within(rows[2]).queryByText("SYNTHETIC/REFERENCE")).toBeNull();
    expect(within(rows[2]).getByText("database")).toBeTruthy();
  });

  it("shows CONFLICT and UNRESOLVED as they are, never a chosen value", async () => {
    renderOperations(READ_KEYS);
    fireEvent.click(await screen.findByRole("button", { name: "finops:Capture CAP-1" }));
    const resolved = await screen.findByRole("table", { name: "Resolved values" });
    const row = (property: string) =>
      within(resolved).getByText(property).closest("tr") as HTMLElement;
    expect(within(row("finops:amount")).getByText("100.000000 USD")).toBeTruthy();
    expect(within(row("finops:lifecycleStatus")).getByText("No value: CONFLICT")).toBeTruthy();
    expect(within(row("finops:lifecycleStatus")).queryByText(/COMPLETED|PENDING/)).toBeNull();
    expect(within(row("finops:orderReference")).getByText("No value: UNRESOLVED")).toBeTruthy();
  });

  it("starts a run with domain_run.create", async () => {
    const api = renderOperations(["domain_run.read", "domain_run.create"], {
      "POST /api/domain-runs": { status: 201, body: RUN },
    });
    fireEvent.click(await screen.findByRole("button", { name: "Start run" }));
    expect(await screen.findByText("Run finished.")).toBeTruthy();
    expect(api.calls.map((call) => call.key)).toContain("POST /api/domain-runs");
  });

  it("triages a case along the state machine and needs a reason to dismiss", async () => {
    const api = renderOperations([...READ_KEYS, "case.triage"], {
      "PATCH /api/domain-cases/c-1": { status: 200, body: caseOf({ status: "INVESTIGATING" }) },
    });
    fireEvent.click(await screen.findByRole("button", { name: "Triage SETTLEMENT_VARIANCE c-1" }));
    const move = screen.getByLabelText("Move to") as HTMLSelectElement;
    expect([...move.options].map((option) => option.value)).toEqual([
      "",
      "INVESTIGATING",
      "DISMISSED",
    ]);

    fireEvent.change(move, { target: { value: "DISMISSED" } });
    fireEvent.click(screen.getByRole("button", { name: "Save triage" }));
    expect(await screen.findByText("Give a reason.")).toBeTruthy();
    expect(api.sent("PATCH /api/domain-cases/c-1")).toEqual([]);

    fireEvent.change(move, { target: { value: "INVESTIGATING" } });
    fireEvent.change(screen.getByLabelText("Comment"), { target: { value: "Checking the bank" } });
    fireEvent.click(screen.getByRole("button", { name: "Save triage" }));
    await waitFor(() =>
      expect(api.sent("PATCH /api/domain-cases/c-1")).toEqual([
        { status: "INVESTIGATING", comment: "Checking the bank" },
      ]),
    );
    expect(await screen.findByText("SETTLEMENT_VARIANCE case updated.")).toBeTruthy();
  });

  it("reopens only a closed case, with a reason", async () => {
    const api = renderOperations([...READ_KEYS, "case.triage"], {
      "GET /api/domain-cases": { status: 200, body: [caseOf({ status: "CLOSED" })] },
      "POST /api/domain-cases/c-1/reopen": { status: 200, body: caseOf({ status: "OPEN" }) },
    });
    fireEvent.click(await screen.findByRole("button", { name: "Triage SETTLEMENT_VARIANCE c-1" }));
    expect(screen.queryByLabelText("Move to")).toBeNull();
    expect(screen.queryByRole("button", { name: "Save triage" })).toBeNull();
    fireEvent.change(screen.getByLabelText(/Reason/), { target: { value: "Bank re-sent" } });
    fireEvent.click(screen.getByRole("button", { name: "Reopen" }));
    await waitFor(() =>
      expect(api.sent("POST /api/domain-cases/c-1/reopen")).toEqual([{ reason: "Bank re-sent" }]),
    );
  });

  it("reports a refused triage", async () => {
    renderOperations([...READ_KEYS, "case.triage"], {
      "PATCH /api/domain-cases/c-1": apiError(409, "INVALID_CASE_TRANSITION", "Not allowed."),
    });
    fireEvent.click(await screen.findByRole("button", { name: "Triage SETTLEMENT_VARIANCE c-1" }));
    fireEvent.change(screen.getByLabelText("Move to"), { target: { value: "INVESTIGATING" } });
    fireEvent.click(screen.getByRole("button", { name: "Save triage" }));
    expect(await screen.findByText("Not allowed.")).toBeTruthy();
  });

  it("accepts or rejects an ambiguous identity link with a reason", async () => {
    const api = renderOperations([...READ_KEYS, "entity_resolution.review"], {
      [`POST /api/entity-resolution/${DECISION.id}/decision`]: { status: 200, body: {} },
    });
    const accept = await screen.findByRole("button", { name: "Accept e-ord-6" });
    fireEvent.click(accept);
    expect(await screen.findByText("Give a reason.")).toBeTruthy();
    expect(api.sent(`POST /api/entity-resolution/${DECISION.id}/decision`)).toEqual([]);

    fireEvent.change(screen.getByLabelText("Reason (required)"), {
      target: { value: "PO-6 is the provider order" },
    });
    fireEvent.click(accept);
    expect(await screen.findByText("Identity link accepted.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Reject, leave unlinked" }));
    expect(await screen.findByText("Identity link rejected.")).toBeTruthy();
    expect(api.sent(`POST /api/entity-resolution/${DECISION.id}/decision`)).toEqual([
      { outcome: "ACCEPT", candidate_entity_id: "e-ord-6", reason: "PO-6 is the provider order" },
      { outcome: "REJECT", reason: "PO-6 is the provider order" },
    ]);
  });

  describe("Create playbook request", () => {
    const REVIEW = caseOf({ status: "HUMAN_REVIEW", case_type: "DUPLICATE_CAPTURE" });
    const PLAYBOOK = "finops:PB-002";
    const CREATE = `POST /api/playbooks/${encodeURIComponent(PLAYBOOK)}/requests`;
    const scoped = (scopes: string[]): Reply => ({
      status: 200,
      body: {
        permissions: [
          ...["domain_run.read", "case.read", "investigation.read"].map((key) => ({
            key,
            tenant_wide: true,
            scopes: [],
            denied_scopes: [],
          })),
          {
            key: "playbook.request",
            tenant_wide: false,
            scopes: scopes.map((id) => ({ type: "playbook", id })),
            denied_scopes: [],
          },
        ],
      },
    });
    const routes = (scopes: string[], create?: Reply | Reply[]) => ({
      [`GET /api/me/permissions?tenant_id=${T}`]: scoped(scopes),
      "GET /api/domain-cases": { status: 200, body: [REVIEW] },
      [`GET /api/investigations?case_id=${REVIEW.id}`]: {
        status: 200,
        body: [
          { investigation_id: "i-2", status: "RUNNING", recommended_playbook: null },
          { investigation_id: "i-1", status: "SUCCEEDED", recommended_playbook: PLAYBOOK },
        ],
      },
      ...(create ? { [CREATE]: create } : {}),
    });
    const open = async () =>
      fireEvent.click(
        await screen.findByRole("button", {
          name: `Request playbook for DUPLICATE_CAPTURE ${REVIEW.id}`,
        }),
      );

    it("sends only the case, under one Idempotency-Key per form", async () => {
      const api = renderOperations([], routes([PLAYBOOK], [
        apiError(503, "UNAVAILABLE", "Try again."),
        {
          status: 201,
          body: {
            id: "req-1",
            status: "NEEDS_APPROVAL",
            target: { capture_id: "CAP-B", amount: "25.00", currency: "USD" },
            decisions: [
              {
                stage: "REQUEST",
                conditions: {
                  proof: {
                    order_reference: "ORD-7",
                    original: { capture_id: "CAP-A", captured_at: "2026-11-11T09:00:00Z" },
                    duplicate: { capture_id: "CAP-B", captured_at: "2026-11-11T09:00:05Z" },
                    rule: "two distinct PayPal captures for one merchant order",
                  },
                },
              },
            ],
          },
        },
      ]));
      await open();
      const form = await screen.findByRole("region", { name: "Playbook request" });
      fireEvent.click(await within(form).findByRole("button", { name: "Create playbook request" }));
      expect((await within(form).findByRole("alert")).textContent).toBe("Try again.");
      fireEvent.click(within(form).getByRole("button", { name: "Create playbook request" }));
      expect(await within(form).findByText(/Request created/)).toBeTruthy();
      expect(within(form).getByRole("link", { name: "Open the request" }).getAttribute("href"))
        .toBe("/playbooks?request=req-1");
      // PB-002's duplicate proof is shown as soon as the request exists (task 5.16).
      const proof = within(form).getByRole("region", { name: "Duplicate capture proof" });
      expect(within(proof).getByText("CAP-B")).toBeTruthy();
      expect(within(proof).getByText("25.00 USD")).toBeTruthy();

      const bodies = api.sent(CREATE);
      expect(bodies).toEqual([
        { case_id: REVIEW.id, parameters: {} },
        { case_id: REVIEW.id, parameters: {} },
      ]);
      const keys = api.calls
        .filter((call) => call.key === CREATE)
        .map((call) => (call.init.headers as Record<string, string>)["Idempotency-Key"]);
      expect(keys[0]).toBeTruthy();
      expect(keys[1]).toBe(keys[0]);
    });

    it("shows why an ineligible case was refused", async () => {
      renderOperations([], routes([PLAYBOOK], {
        status: 422,
        body: {
          error: {
            code: "PLAYBOOK_INELIGIBLE",
            message: "The playbook's trigger or preconditions do not hold for this case.",
            failures: ["a refund already exists for the duplicate capture"],
          },
        },
      }));
      await open();
      fireEvent.click(await screen.findByRole("button", { name: "Create playbook request" }));
      expect((await screen.findByRole("alert")).textContent).toContain(
        "a refund already exists for the duplicate capture",
      );
    });

    it("offers nothing for a playbook outside the holder's scope", async () => {
      const api = renderOperations([], routes(["finops:PB-001"]));
      await open();
      expect(await screen.findByText(`You cannot request ${PLAYBOOK}.`)).toBeTruthy();
      expect(screen.queryByRole("button", { name: "Create playbook request" })).toBeNull();
      expect(api.sent(CREATE)).toEqual([]);
    });

    it("offers no request on a case that is not under human review", async () => {
      renderOperations([], {
        ...routes([PLAYBOOK]),
        "GET /api/domain-cases": { status: 200, body: [{ ...REVIEW, status: "OPEN" }] },
      });
      expect(await screen.findByRole("cell", { name: "DUPLICATE_CAPTURE" })).toBeTruthy();
      expect(screen.queryByRole("button", { name: /Request playbook/ })).toBeNull();
    });
  });
});
