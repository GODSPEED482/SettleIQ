// @vitest-environment jsdom
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { apiError } from "../../test/api";
import { stubLayout } from "../../test/reactflow";
import { stubLocalStorage } from "../../test/storage";
import { DRAFT, renderStudio } from "./studioFixture";

beforeEach(() => {
  stubLocalStorage();
  stubLayout();
});
afterEach(() => vi.unstubAllGlobals());

const relationshipRow = (label: string) =>
  screen.getByText(label, { selector: "td" }).closest("tr") as HTMLElement;

describe("OntologyStudio graph", () => {
  it("shows the draft read-only to a holder of ontology.read alone", async () => {
    const api = renderStudio([]);
    expect(await screen.findByText("Open draft at revision 3.")).toBeTruthy();
    expect(within(relationshipRow("has part")).getByText("pack")).toBeTruthy();
    expect(within(relationshipRow("made of")).getByText("tenant")).toBeTruthy();
    expect(screen.queryByRole("form", { name: "Add a relationship" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Remove/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Accept Gadget" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Abandon draft" })).toBeNull();
    expect(api.calls.every((call) => call.key.startsWith("GET "))).toBe(true);
  });

  it("starts a draft when none is open", async () => {
    const api = renderStudio(["ontology.edit"], {
      "GET /api/ontology/drafts/open": [
        apiError(404, "NOT_FOUND"),
        { status: 200, body: DRAFT },
      ],
      "POST /api/ontology/drafts": { status: 201, body: DRAFT },
    });
    fireEvent.click(await screen.findByRole("button", { name: "Start a draft" }));
    expect(await screen.findByText("Draft started.")).toBeTruthy();
    expect(api.sent("POST /api/ontology/drafts")).toHaveLength(1);
  });

  it("adds a relationship against the revision it was made on", async () => {
    const api = renderStudio(["ontology.edit"], {
      "PATCH /api/ontology/drafts/d-1": { status: 200, body: { ...DRAFT, revision: 4 } },
    });
    const form = await screen.findByRole("form", { name: "Add a relationship" });
    fireEvent.change(within(form).getByLabelText("Name"), { target: { value: "fits into" } });
    fireEvent.change(within(form).getByLabelText("From"), { target: { value: "pk:Part" } });
    fireEvent.change(within(form).getByLabelText("To"), { target: { value: "tenant:Widget" } });
    fireEvent.click(within(form).getByRole("button", { name: "Add relationship" }));
    expect(await screen.findByText("fits into added to the draft.")).toBeTruthy();
    const [body] = api.sent("PATCH /api/ontology/drafts/d-1");
    expect(body.expected_revision).toBe(3);
    expect(body.graph).toHaveLength(3);
    expect(body.graph[2]).toEqual({
      "@id": "tenant:fitsInto",
      "@type": "owl:ObjectProperty",
      "rdfs:label": "fits into",
      "rdfs:domain": "pk:Part",
      "rdfs:range": "tenant:Widget",
    });
  });

  it("removes only tenant relationships", async () => {
    const api = renderStudio(["ontology.edit"], {
      "PATCH /api/ontology/drafts/d-1": { status: 200, body: DRAFT },
    });
    await screen.findByText("Open draft at revision 3.");
    expect(within(relationshipRow("has part")).queryByRole("button")).toBeNull();
    fireEvent.click(within(relationshipRow("made of")).getByRole("button", { name: "Remove made of" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm remove made of" }));
    await screen.findByText("Relationship removed.");
    const [body] = api.sent("PATCH /api/ontology/drafts/d-1");
    expect(body.graph.map((node: { "@id": string }) => node["@id"])).toEqual(["tenant:Widget"]);
  });

  it("reloads the draft when an edit is stale", async () => {
    const api = renderStudio(["ontology.edit"], {
      "PATCH /api/ontology/drafts/d-1": apiError(409, "STALE_DRAFT", "The draft changed."),
    });
    await screen.findByText("Open draft at revision 3.");
    fireEvent.click(within(relationshipRow("made of")).getByRole("button", { name: "Remove made of" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm remove made of" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/It has been reloaded/);
    await waitFor(() =>
      expect(api.calls.filter((call) => call.key === "GET /api/ontology/drafts/open")).toHaveLength(2),
    );
  });
});

describe("OntologyStudio proposals", () => {
  it("labels evidence observed or inferred and keeps the model's reading apart", async () => {
    renderStudio([]);
    const panel = await screen.findByRole("region", { name: "Generated proposals" });
    const gadget = within(panel).getByText("Gadget").closest("tr") as HTMLElement;
    expect(within(gadget).getByText("observed")).toBeTruthy();
    expect(within(gadget).getByText("inferred")).toBeTruthy();
    expect(within(gadget).getByText("kind is a field in the discovered metadata.", { exact: false })).toBeTruthy();
    expect(within(gadget).getByText("Model's reading: values look like gadget types")).toBeTruthy();
    expect(within(panel).getByText("items.size", { exact: false })).toBeTruthy();
  });

  it("never accepts a proposal without a person's action", async () => {
    const api = renderStudio(["ontology.edit"], {
      "PATCH /api/ontology/drafts/d-1": { status: 200, body: DRAFT },
    });
    const accept = await screen.findByRole("button", { name: "Accept Gadget" });
    expect(api.sent("PATCH /api/ontology/drafts/d-1")).toHaveLength(0);
    fireEvent.click(accept);
    await screen.findByText("Gadget accepted into the draft.");
    const [body] = api.sent("PATCH /api/ontology/drafts/d-1");
    expect(body.expected_revision).toBe(3);
    expect(body.graph[2]).toEqual({
      "@id": "tenant:Gadget",
      "@type": "owl:Class",
      "rdfs:label": "Gadget",
      "rdfs:subClassOf": "pk:Thing",
    });
  });

  it("marks a proposal already in the draft as accepted", async () => {
    const accepted = {
      ...DRAFT,
      tenant_extension: {
        "@graph": [
          ...DRAFT.tenant_extension["@graph"],
          { "@id": "tenant:Gadget", "@type": "owl:Class", "rdfs:label": "Gadget" },
        ],
      },
    };
    renderStudio(["ontology.edit"], { "GET /api/ontology/drafts/open": { status: 200, body: accepted } });
    const panel = await screen.findByRole("region", { name: "Generated proposals" });
    expect(within(panel).getByText("Accepted")).toBeTruthy();
    expect(within(panel).queryByRole("button", { name: "Accept Gadget" })).toBeNull();
  });

  it("follows a generation job until its proposals are ready", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const api = renderStudio(["ontology.generate"], {
        "POST /api/ontology/generate": { status: 202, body: { job_id: "j-1", status: "QUEUED", error: null } },
        "GET /api/ontology/generation-jobs/j-1": [
          { status: 200, body: { job_id: "j-1", status: "RUNNING", error: null } },
          { status: 200, body: { job_id: "j-1", status: "SUCCEEDED", error: null } },
        ],
      });
      fireEvent.click(await screen.findByRole("button", { name: "Generate proposals" }));
      await screen.findByText("Generation queued…");
      await vi.advanceTimersByTimeAsync(2100);
      await vi.advanceTimersByTimeAsync(2100);
      expect(await screen.findByText("Proposals are ready to review.")).toBeTruthy();
      expect(api.calls.filter((call) => call.key.includes("generation-jobs"))).toHaveLength(2);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("OntologyStudio domain packs", () => {
  const PACKS = [
    { namespace: "pk", version: "1.0.0", status: "ACTIVE", case_types: ["THING_MISMATCH"] },
    { namespace: "other", version: "0.1.0", status: "AVAILABLE", case_types: [] },
  ];

  it("lists packs read-only to a holder of domain_pack.read alone", async () => {
    const api = renderStudio(["domain_pack.read"], {
      "GET /api/domain-packs": { status: 200, body: PACKS },
    });
    const panel = await screen.findByRole("region", { name: "Domain packs" });
    expect(await within(panel).findByText("THING_MISMATCH")).toBeTruthy();
    expect(within(panel).getByText("Available")).toBeTruthy();
    expect(within(panel).queryByRole("button")).toBeNull();
    expect(api.calls.every((call) => call.key.startsWith("GET "))).toBe(true);
  });

  it("activates and disables a pack under domain_pack.manage, sending only the namespace and action", async () => {
    const api = renderStudio(["domain_pack.read", "domain_pack.manage"], {
      "GET /api/domain-packs": { status: 200, body: PACKS },
      "POST /api/domain-packs": { status: 200, body: { namespace: "other", status: "ACTIVE" } },
    });
    fireEvent.click(await screen.findByRole("button", { name: "Activate other" }));
    expect(await screen.findByText("other activated.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Disable pk" }));
    expect(await screen.findByText("pk disabled.")).toBeTruthy();
    expect(api.sent("POST /api/domain-packs")).toEqual([
      { namespace: "other", action: "ACTIVATE" },
      { namespace: "pk", action: "DISABLE" },
    ]);
  });

  it("warns when the open draft was started without an active pack", async () => {
    renderStudio(["domain_pack.read"], {
      "GET /api/domain-packs": {
        status: 200,
        body: PACKS.map((pack) => ({ ...pack, status: "ACTIVE" })),
      },
    });
    const panel = await screen.findByRole("region", { name: "Domain packs" });
    expect(
      await within(panel).findByText(/does not include other\. Abandon it and start a new draft/),
    ).toBeTruthy();
  });

  it("does not warn when the draft composes exactly the active packs", async () => {
    renderStudio(["domain_pack.read"], {
      "GET /api/domain-packs": { status: 200, body: PACKS },
    });
    const panel = await screen.findByRole("region", { name: "Domain packs" });
    await within(panel).findByText("THING_MISMATCH");
    expect(within(panel).queryByText(/start a new draft/)).toBeNull();
  });
});
