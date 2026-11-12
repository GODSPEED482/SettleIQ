// @vitest-environment jsdom
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { stubLayout } from "../../test/reactflow";
import { stubLocalStorage } from "../../test/storage";
import { DRAFT, renderStudio } from "./studioFixture";
import type { Authority, Mapping, SourceSummary, ValidationRun } from "./types";

const SOURCES: SourceSummary[] = [
  { id: "s-1", display_name: "Shop DB", connector_id: "merchant-postgres", status: "ACTIVE" },
  { id: "s-2", display_name: "Bank files", connector_id: "tenant-csv", status: "ACTIVE" },
];
const MAPPING: Mapping = {
  id: "m-1",
  version: 1,
  source_id: "s-1",
  external_object: "items",
  field_path: "weight",
  property_iri: "pk:size",
  class_iri: "pk:Thing",
  datatype: "decimal",
  transform: { name: "decimal_string" },
  nullable: false,
  is_unique: false,
  sync_strategy: "batch",
  value_basis: "measured",
  unit: "kg",
  currency_field: null,
  temporal_context: "as_of_read",
  method: "human",
};
const OTHER: Mapping = { ...MAPPING, id: "m-2", field_path: "colour", property_iri: "tenant:colour" };
const ASSERTION: Authority = {
  id: "a-1",
  version: 1,
  property_iri: "pk:size",
  entity_type_iri: "pk:Thing",
  source_id: "s-2",
  valid_from: "2026-01-01T00:00:00+00:00",
  valid_to: null,
  verification_method: "contract",
};
const FAILED_RUN: ValidationRun = {
  validation_run_id: "r-1",
  draft_id: "d-1",
  revision: 3,
  content_hash: DRAFT.content_hash,
  passed: false,
  results: [
    {
      validator: "mappings.comparable",
      kind: "platform",
      passed: false,
      messages: ["pk:size: bindings differ in datatype, value basis, unit, currency or time context."],
    },
    {
      validator: "mappings.terms_exist",
      kind: "platform",
      passed: false,
      messages: ["Unknown ontology term: tenant:ghost"],
    },
    { validator: "core.shapes", kind: "shacl", passed: true, messages: [] },
  ],
};

const READ = ["mapping.read", "authority.read", "source.read"];
const EDIT = [...READ, "mapping.edit", "authority.edit", "ontology.validate"];

function routes(extra = {}) {
  return {
    "GET /api/mappings?draft_id=d-1": { status: 200, body: [MAPPING, OTHER] },
    "GET /api/authority?draft_id=d-1": { status: 200, body: [ASSERTION] },
    "GET /api/sources": { status: 200, body: SOURCES },
    ...extra,
  };
}

beforeEach(() => {
  stubLocalStorage();
  stubLayout();
});
afterEach(() => vi.unstubAllGlobals());

const panel = (name: string) => screen.findByRole("region", { name });
const rowOf = (container: HTMLElement, text: string) =>
  within(container).getByText(text, { exact: false }).closest("tr") as HTMLElement;

describe("binding editor", () => {
  it("lists bindings read-only without mapping.edit", async () => {
    renderStudio(READ, routes());
    const bindings = await panel("Bindings");
    const row = await waitFor(() => rowOf(bindings, "items.weight"));
    expect(within(row).getByText("Shop DB", { exact: false })).toBeTruthy();
    expect(within(bindings).queryByRole("form")).toBeNull();
    expect(within(bindings).queryByRole("button", { name: "Retire" })).toBeNull();
  });

  it("saves a binding with every field the API takes", async () => {
    const api = renderStudio(EDIT, routes({ "POST /api/mappings": { status: 201, body: {} } }));
    const form = await screen.findByRole("form", { name: "Add a binding" });
    await within(form).findByRole("option", { name: "Shop DB (merchant-postgres)" });
    const set = (label: string, value: string) =>
      fireEvent.change(within(form).getByLabelText(label), { target: { value } });
    set("Source", "s-1");
    set("Object", "items");
    set("Field", "status");
    set("Class", "pk:Thing");
    set("Property", "pk:size");
    set("Data type", "string");
    set("Transform", "enum_map");
    set("Sync", "event");
    set("Value basis", "reported");
    set("Time context", "event_time");
    set("Unit", "");
    set("Currency field", "");
    fireEvent.change(within(form).getByRole("textbox", { name: /Value map/ }), {
      target: { value: "S=small\nL=large" },
    });
    fireEvent.click(within(form).getByLabelText("Nullable"));
    fireEvent.click(within(form).getByRole("button", { name: "Save binding" }));
    expect(await screen.findByText("Binding saved.")).toBeTruthy();
    expect(api.sent("POST /api/mappings")).toEqual([
      {
        draft_id: "d-1",
        source_id: "s-1",
        external_object: "items",
        field_path: "status",
        property_iri: "pk:size",
        class_iri: "pk:Thing",
        datatype: "string",
        transform: { name: "enum_map", values: { S: "small", L: "large" } },
        nullable: true,
        is_unique: false,
        sync_strategy: "event",
        value_basis: "reported",
        unit: null,
        currency_field: null,
        temporal_context: "event_time",
        method: "human",
      },
    ]);
  });

  it("retires a binding after confirmation", async () => {
    const api = renderStudio(EDIT, routes({ "POST /api/mappings/m-1/retire": { status: 200, body: {} } }));
    const bindings = await panel("Bindings");
    const row = await waitFor(() => rowOf(bindings, "items.weight"));
    fireEvent.click(within(row).getByRole("button", { name: "Retire" }));
    fireEvent.click(within(row).getByRole("button", { name: "Confirm retire" }));
    expect(await screen.findByText("weight binding retired.")).toBeTruthy();
    expect(api.calls.map((call) => call.key)).toContain("POST /api/mappings/m-1/retire");
  });

  it("shows each mappings validator message next to the row it names", async () => {
    renderStudio(EDIT, routes({ "POST /api/ontology/validate": { status: 200, body: FAILED_RUN } }));
    fireEvent.click(await screen.findByRole("button", { name: "Validate draft" }));
    await screen.findByText("Validation failed", { exact: false });
    const bindings = await panel("Bindings");
    const sized = rowOf(bindings, "items.weight");
    expect(within(sized).getByText(/pk:size: bindings differ/)).toBeTruthy();
    expect(within(rowOf(bindings, "items.colour")).queryByText(/bindings differ/)).toBeNull();
    expect(within(bindings).getByText("Unknown ontology term: tenant:ghost")).toBeTruthy();
    const authority = await panel("Authority");
    expect(within(rowOf(authority, "contract")).getByText(/pk:size: bindings differ/)).toBeTruthy();
  });

  it("prefills a binding from a proposal, marked as accepted from it", async () => {
    const api = renderStudio(EDIT, routes({ "POST /api/mappings": { status: 201, body: {} } }));
    fireEvent.click(await screen.findByRole("button", { name: "Use as binding" }));
    const form = await screen.findByRole("form", { name: "Add a binding" });
    expect(within(form).getByRole("heading", { name: "Bind the accepted proposal" })).toBeTruthy();
    expect((within(form).getByLabelText("Field") as HTMLInputElement).value).toBe("size");
    fireEvent.change(within(form).getByLabelText("Class"), { target: { value: "pk:Thing" } });
    fireEvent.click(within(form).getByRole("button", { name: "Save binding" }));
    await screen.findByText("Binding saved.");
    expect(api.sent("POST /api/mappings")[0]).toMatchObject({
      source_id: "s-1",
      external_object: "items",
      field_path: "size",
      property_iri: "pk:size",
      method: "accepted_proposal",
    });
  });

  it("marks a proposal that is already bound", async () => {
    const bound = { ...MAPPING, id: "m-3", field_path: "size" };
    renderStudio(EDIT, routes({ "GET /api/mappings?draft_id=d-1": { status: 200, body: [bound] } }));
    const proposals = await panel("Generated proposals");
    expect(await within(proposals).findByText("Bound")).toBeTruthy();
    expect(within(proposals).queryByRole("button", { name: "Use as binding" })).toBeNull();
  });
});

describe("authority editor", () => {
  it("saves an assertion with zoned times", async () => {
    const api = renderStudio(EDIT, routes({ "POST /api/authority": { status: 201, body: {} } }));
    const form = await screen.findByRole("form", { name: "Add an authority assertion" });
    await within(form).findByRole("option", { name: "Bank files (tenant-csv)" });
    const set = (label: string, value: string) =>
      fireEvent.change(within(form).getByLabelText(label), { target: { value } });
    set("Property", "pk:size");
    set("Entity type", "pk:Part");
    set("Source", "s-2");
    set("Valid from", "2026-11-01T09:00");
    set("Verified by", "signed contract");
    fireEvent.click(within(form).getByRole("button", { name: "Save assertion" }));
    await screen.findByText("Authority assertion saved.");
    const [body] = api.sent("POST /api/authority");
    expect(body).toEqual({
      draft_id: "d-1",
      property_iri: "pk:size",
      entity_type_iri: "pk:Part",
      source_id: "s-2",
      valid_from: new Date("2026-11-01T09:00").toISOString(),
      valid_to: null,
      verification_method: "signed contract",
    });
    expect(body.valid_from).toMatch(/Z$/);
  });

  it("retires an assertion, and offers no edits without authority.edit", async () => {
    const api = renderStudio(EDIT, routes({ "POST /api/authority/a-1/retire": { status: 200, body: {} } }));
    const authority = await panel("Authority");
    const row = await waitFor(() => rowOf(authority, "contract"));
    fireEvent.click(within(row).getByRole("button", { name: "Retire" }));
    fireEvent.click(within(row).getByRole("button", { name: "Confirm retire" }));
    await screen.findByText("Authority assertion retired.");
    expect(api.calls.map((call) => call.key)).toContain("POST /api/authority/a-1/retire");
  });

  it("hides the authority form from a reader", async () => {
    renderStudio(READ, routes());
    const authority = await panel("Authority");
    await within(authority).findByText("contract", { exact: false });
    expect(within(authority).queryByRole("form")).toBeNull();
  });
});

const COMPLETE = {
  class_iri: "pk:Thing",
  datatype: "decimal" as const,
  transform: { name: "decimal_string" as const },
  value_basis: "signed",
  temporal_context: "at_event",
  currency_field: "currency",
  unit: null,
  nullable: false,
  is_unique: false,
  sync_strategy: "batch" as const,
};
const proposed = (field: string, property: string) => ({
  type: "binding" as const,
  source_id: "s-1",
  object: "items",
  field,
  property,
  class_iri: "pk:Thing",
  mapping: COMPLETE,
  confidence: "high" as const,
  status: "PROPOSED",
  basis: "inferred" as const,
  evidence: [{ kind: "field_profile" as const, field, detail: `${field} is a field.`, basis: "observed" as const }],
});
const withProposals = (proposals: unknown[]) => ({
  "GET /api/ontology/drafts/open": { status: 200, body: { ...DRAFT, proposals } },
});

describe("complete binding proposals", () => {
  it("accepts a complete proposal in one action, with every field the server filled", async () => {
    const api = renderStudio(
      EDIT,
      routes({ ...withProposals([proposed("size", "pk:size")]), "POST /api/mappings": { status: 201, body: {} } }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Accept binding" }));
    await screen.findByText("Binding saved.");
    expect(api.sent("POST /api/mappings")).toEqual([
      {
        ...COMPLETE,
        draft_id: "d-1",
        source_id: "s-1",
        external_object: "items",
        field_path: "size",
        property_iri: "pk:size",
        method: "accepted_proposal",
      },
    ]);
  });

  it("accepts every ready proposal at once, leaving one that names a term the draft lacks", async () => {
    const api = renderStudio(
      EDIT,
      routes({
        ...withProposals([proposed("size", "pk:size"), proposed("length", "pk:size"), proposed("hue", "tenant:hue")]),
        "POST /api/mappings": { status: 201, body: {} },
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Accept all 2 ready bindings" }));
    await screen.findByText("2 proposed bindings accepted.");
    expect(api.sent("POST /api/mappings").map((body) => body.field_path)).toEqual(["size", "length"]);
    const proposals = await panel("Generated proposals");
    const hue = rowOf(proposals, "items.hue");
    expect(within(hue).queryByRole("button", { name: "Accept binding" })).toBeNull();
    expect(within(hue).getByRole("button", { name: "Use as binding" })).toBeTruthy();
  });

  it("offers only the editor for a proposal generated without a complete mapping", async () => {
    renderStudio(EDIT, routes());
    expect(await screen.findByRole("button", { name: "Use as binding" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Accept binding" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Accept all/ })).toBeNull();
  });
});

const PACK = {
  namespace: "pk",
  version: "1.0.0",
  status: "ACTIVE",
  case_types: [],
  mapping_hints: [
    {
      kind: "binding",
      connector_id: "merchant-postgres",
      external_object: "items",
      class_iri: "pk:Thing",
      sync_strategy: "batch",
      fields: [
        // Already on the draft as MAPPING, so not offered again.
        { ...MAPPING, id: undefined, version: undefined, source_id: undefined },
        {
          field_path: "$.size",
          property_iri: "pk:size",
          datatype: "decimal",
          transform: { name: "decimal_string" },
          value_basis: "measured",
          temporal_context: "as_of_read",
          currency_field: null,
          unit: "kg",
          nullable: true,
          is_unique: false,
        },
      ],
    },
    {
      kind: "authority",
      connector_id: "merchant-postgres",
      property_iri: "pk:size",
      entity_type_iri: "pk:Thing",
      verification_method: "shop database row",
    },
  ],
};

describe("pack bindings", () => {
  const packRoutes = (extra = {}) =>
    routes({ "GET /api/domain-packs": { status: 200, body: [PACK] }, ...extra });

  it("applies an active pack's bindings and authority to the source on its connector only", async () => {
    const api = renderStudio(
      [...EDIT, "domain_pack.read"],
      packRoutes({
        "POST /api/mappings": { status: 201, body: {} },
        "POST /api/authority": { status: 201, body: {} },
      }),
    );
    const section = await panel("Pack bindings");
    expect(within(section).getByText(/pk for Shop DB: 1 bindings, 1 authority assertions/)).toBeTruthy();
    expect(within(section).queryByText(/Bank files/)).toBeNull();
    fireEvent.click(within(section).getByRole("button", { name: "Apply pk bindings to Shop DB" }));
    await screen.findByText("pk bindings applied to Shop DB.");
    expect(api.sent("POST /api/mappings")).toEqual([
      expect.objectContaining({
        draft_id: "d-1",
        source_id: "s-1",
        external_object: "items",
        field_path: "$.size",
        property_iri: "pk:size",
        class_iri: "pk:Thing",
        unit: "kg",
        method: "accepted_proposal",
      }),
    ]);
    expect(api.sent("POST /api/authority")).toEqual([
      expect.objectContaining({
        draft_id: "d-1",
        source_id: "s-1",
        property_iri: "pk:size",
        entity_type_iri: "pk:Thing",
        valid_to: null,
        verification_method: "shop database row",
      }),
    ]);
  });

  it("offers nothing for a pack that is not active", async () => {
    renderStudio(
      [...EDIT, "domain_pack.read"],
      routes({ "GET /api/domain-packs": { status: 200, body: [{ ...PACK, status: "AVAILABLE" }] } }),
    );
    await panel("Bindings");
    expect(screen.queryByRole("region", { name: "Pack bindings" })).toBeNull();
  });
});

describe("pack gaps", () => {
  const REQUIRING = {
    ...PACK,
    mapping_hints: [],
    identities: { "pk:Thing": ["pk:ref"], "pk:Part": ["pk:ref"] },
    relationships: [{ subject: "pk:Part", predicate: "pk:hasPart", object: "pk:Thing", via: "pk:ref" }],
    required_properties: [{ class_iri: "pk:Thing", property_iri: "pk:size", message: "A thing needs a size." }],
  };
  const bind = (id: string, cls: string, property: string): Mapping => ({
    ...MAPPING,
    id,
    class_iri: cls,
    property_iri: property,
    field_path: id,
  });

  it("names what the pack's run would skip with the draft's bindings", async () => {
    renderStudio(
      [...READ, "domain_pack.read"],
      routes({
        "GET /api/domain-packs": { status: 200, body: [REQUIRING] },
        "GET /api/mappings?draft_id=d-1": {
          status: 200,
          body: [bind("a", "pk:Thing", "pk:colour"), bind("b", "pk:Part", "pk:ref"), bind("c", "pk:Gizmo", "pk:size")],
        },
      }),
    );
    const section = await panel("Pack bindings");
    expect(within(section).getByText(/pk:Gizmo is not a class pk resolves/)).toBeTruthy();
    expect(within(section).getByText("Nothing identifies a pk:Thing: bind pk:ref on it.")).toBeTruthy();
    expect(within(section).getByText("A thing needs a size. Bind pk:size on pk:Thing.")).toBeTruthy();
    expect(within(section).getByText("pk:Part links to pk:Thing only through pk:ref, bound on both.")).toBeTruthy();
  });

  it("says nothing once the bindings give the run what it needs", async () => {
    renderStudio(
      [...READ, "domain_pack.read"],
      routes({
        "GET /api/domain-packs": { status: 200, body: [REQUIRING] },
        "GET /api/mappings?draft_id=d-1": {
          status: 200,
          body: [bind("a", "pk:Thing", "pk:ref"), bind("b", "pk:Thing", "pk:size"), bind("c", "pk:Part", "pk:ref")],
        },
      }),
    );
    await panel("Bindings");
    expect(screen.queryByRole("region", { name: "Pack bindings" })).toBeNull();
  });

  it("warns when nothing is bound to an active pack", async () => {
    renderStudio(
      [...READ, "domain_pack.read"],
      routes({
        "GET /api/domain-packs": { status: 200, body: [REQUIRING] },
        "GET /api/mappings?draft_id=d-1": { status: 200, body: [] },
      }),
    );
    expect(
      await within(await panel("Pack bindings")).findByText(
        "Nothing is bound to a pk class, so its run has nothing to check.",
      ),
    ).toBeTruthy();
  });
});
