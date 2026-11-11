// @vitest-environment jsdom
import { fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { apiError } from "../../test/api";
import { stubLayout } from "../../test/reactflow";
import { stubLocalStorage } from "../../test/storage";
import { DRAFT, renderStudio } from "./studioFixture";
import type { ValidationRun } from "./types";

const PASSED: ValidationRun = {
  validation_run_id: "r-9",
  draft_id: "d-1",
  revision: 3,
  content_hash: DRAFT.content_hash,
  passed: true,
  results: [
    { validator: "composition.current", kind: "platform", passed: true, messages: [] },
    { validator: "core.shapes", kind: "shacl", passed: true, messages: [] },
    { validator: "pk.rules", kind: "pack", passed: true, messages: [] },
  ],
};
const FAILED: ValidationRun = {
  ...PASSED,
  passed: false,
  results: [
    PASSED.results[0],
    { validator: "core.shapes", kind: "shacl", passed: false, messages: ["tenant:madeOf needs one range"] },
  ],
};
const PUBLISHER = ["ontology.validate", "ontology.publish"];

beforeEach(() => {
  stubLocalStorage();
  stubLayout();
});
afterEach(() => vi.unstubAllGlobals());

async function validate() {
  fireEvent.click(await screen.findByRole("button", { name: "Validate draft" }));
  await screen.findByText("Validation finished.");
}

const publishButton = () => screen.getByRole("button", { name: "Publish version 1" }) as HTMLButtonElement;

describe("publish confirmation", () => {
  it("shows the candidate hash and each validator's result", async () => {
    renderStudio(PUBLISHER, { "POST /api/ontology/validate": { status: 200, body: FAILED } });
    expect(await screen.findByText(DRAFT.content_hash)).toBeTruthy();
    await validate();
    const table = screen.getByRole("table", { name: "Validator results" });
    const shapes = within(table).getByText("core.shapes").closest("tr") as HTMLElement;
    expect(within(shapes).getByText("shacl")).toBeTruthy();
    expect(within(shapes).getByText("Failed")).toBeTruthy();
    expect(within(shapes).getByText("tenant:madeOf needs one range")).toBeTruthy();
    const composition = within(table).getByText("composition.current").closest("tr") as HTMLElement;
    expect(within(composition).getByText("Passed")).toBeTruthy();
  });

  it("stays closed until a run has passed", async () => {
    renderStudio(PUBLISHER, { "POST /api/ontology/validate": { status: 200, body: FAILED } });
    await screen.findByText("Validate the draft before publishing it.");
    expect(publishButton().disabled).toBe(true);
    await validate();
    expect(screen.getByText(/The last validation failed/)).toBeTruthy();
    expect(publishButton().disabled).toBe(true);
  });

  it("closes again when the candidate hash moves after validation", async () => {
    renderStudio(PUBLISHER, {
      "POST /api/ontology/validate": { status: 200, body: PASSED },
      // A saved binding changes the hash but not the revision.
      "GET /api/ontology/drafts/open": [
        { status: 200, body: DRAFT },
        { status: 200, body: { ...DRAFT, content_hash: "b".repeat(64) } },
      ],
    });
    await validate();
    expect(publishButton().disabled).toBe(true);
    expect(screen.getByText(/changed after it was validated/)).toBeTruthy();
    expect(screen.getByText(/this result is out of date/)).toBeTruthy();
  });

  it("publishes the confirmed run with confirm_publish", async () => {
    const api = renderStudio(PUBLISHER, {
      "POST /api/ontology/validate": { status: 200, body: PASSED },
      "POST /api/ontology/publish": {
        status: 201,
        body: { id: "o-1", version: 1, content_hash: DRAFT.content_hash, validation_run_id: "r-9" },
      },
    });
    await validate();
    fireEvent.click(publishButton());
    const confirm = screen.getByRole("group", { name: "Confirm publication" });
    expect(within(confirm).getByText(DRAFT.content_hash)).toBeTruthy();
    expect(within(confirm).getByText("r-9")).toBeTruthy();
    expect(api.sent("POST /api/ontology/publish")).toHaveLength(0);
    fireEvent.click(within(confirm).getByRole("button", { name: "Confirm publish" }));
    expect(await screen.findByText("Version 1 published.")).toBeTruthy();
    expect(api.sent("POST /api/ontology/publish")).toEqual([
      { draft_id: "d-1", validation_run_id: "r-9", confirm_publish: true },
    ]);
  });

  it("can be cancelled without sending anything", async () => {
    const api = renderStudio(PUBLISHER, { "POST /api/ontology/validate": { status: 200, body: PASSED } });
    await validate();
    fireEvent.click(publishButton());
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("group", { name: "Confirm publication" })).toBeNull();
    expect(api.sent("POST /api/ontology/publish")).toHaveLength(0);
  });

  it("reports a refusal from the server", async () => {
    renderStudio(PUBLISHER, {
      "POST /api/ontology/validate": { status: 200, body: PASSED },
      "POST /api/ontology/publish": apiError(409, "VALIDATION_STALE", "The validation run is stale."),
    });
    await validate();
    fireEvent.click(publishButton());
    fireEvent.click(screen.getByRole("button", { name: "Confirm publish" }));
    expect((await screen.findByRole("alert")).textContent).toBe("The validation run is stale.");
  });

  it("is not offered without ontology.publish", async () => {
    renderStudio(["ontology.validate"]);
    await screen.findByRole("button", { name: "Validate draft" });
    expect(screen.queryByRole("region", { name: "Publish" })).toBeNull();
  });
});
