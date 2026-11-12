// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PermissionsProvider } from "../../permissions/PermissionsContext";
import { RequirePermission } from "../../permissions/RequirePermission";
import { SessionProvider } from "../../session/SessionContext";
import { apiError, mockApi, type Reply } from "../../test/api";
import { stubLocalStorage } from "../../test/storage";
import { SourceManagement } from "./SourceManagement";
import { SUPPORT_FLAGS, type ConnectorManifest, type Source, type SupportFlag } from "./types";

const T = "a0000000-0000-4000-8000-000000000000";

const flags = (on: SupportFlag[]) =>
  Object.fromEntries(SUPPORT_FLAGS.map(([flag]) => [flag, on.includes(flag)])) as Record<
    SupportFlag,
    boolean
  >;

const WEBHOOKS = {
  key: "webhooks.receive",
  version: "1.1.0",
  parameters: {
    type: "object",
    properties: {
      event_types: {
        type: "array",
        items: { enum: ["PAYMENT.CAPTURE.COMPLETED", "PAYMENT.CAPTURE.REFUNDED"] },
      },
    },
  },
};

const PAYPAL: ConnectorManifest = {
  connector_id: "paypal",
  version: "1.1.0",
  protocols: ["https-rest", "webhook", "csv"],
  source_types: ["payment_provider"],
  auth_mode: "oauth2_client_credentials",
  secret_reference_required: true,
  extraction_modes: ["backfill"],
  capabilities: [{ key: "captures.read", version: "1.0.0", parameters: {} }, WEBHOOKS],
  ...flags(["supports_batch_extraction", "supports_events", "supports_file_ingestion"]),
};

const CSV: ConnectorManifest = {
  connector_id: "tenant-csv",
  version: "1.0.0",
  protocols: ["csv"],
  source_types: ["tenant_file"],
  auth_mode: "none",
  secret_reference_required: false,
  extraction_modes: [],
  capabilities: [{ key: "files.upload", version: "1.0.0", parameters: {} }],
  ...flags(["supports_discovery", "supports_metadata", "supports_file_ingestion"]),
};

const sourceOf = (overrides: Partial<Source>): Source => ({
  id: "s0000000-0000-4000-8000-000000000001",
  connector_id: "paypal",
  connector_version: "1.1.0",
  source_type: "payment_provider",
  protocol: "https-rest",
  display_name: "PayPal Sandbox",
  config: {},
  capabilities: PAYPAL.capabilities,
  status: "ACTIVE",
  has_secret_reference: false,
  created_at: "2026-11-11T10:00:00Z",
  updated_at: "2026-11-11T10:00:00Z",
  ...overrides,
});

const PAYPAL_SOURCE = sourceOf({});
const CSV_SOURCE = sourceOf({
  id: "s0000000-0000-4000-8000-000000000002",
  connector_id: "tenant-csv",
  connector_version: "1.0.0",
  source_type: "tenant_file",
  protocol: "csv",
  display_name: "Bank statements",
  capabilities: CSV.capabilities,
  has_secret_reference: true,
});

type Grant = string | { key: string; scopes: string[] };

const granted = (grants: Grant[]): Reply => ({
  status: 200,
  body: {
    permissions: grants.map((grant) =>
      typeof grant === "string"
        ? { key: grant, tenant_wide: true, scopes: [], denied_scopes: [] }
        : {
            key: grant.key,
            tenant_wide: false,
            scopes: grant.scopes.map((id) => ({ type: "source", id })),
            denied_scopes: [],
          },
    ),
  },
});

const READ = ["source.read", "connector.read"];

beforeEach(() => {
  stubLocalStorage();
});
afterEach(() => vi.unstubAllGlobals());

function renderSources(grants: Grant[], extra: Record<string, Reply | Reply[]> = {}) {
  const api = mockApi({
    "GET /api/me/tenants": { status: 200, body: { tenants: [{ id: T, name: "Acme" }] } },
    [`GET /api/me/permissions?tenant_id=${T}`]: granted(grants),
    "GET /api/connectors": { status: 200, body: [PAYPAL, CSV] },
    "GET /api/sources": { status: 200, body: [PAYPAL_SOURCE, CSV_SOURCE] },
    ...extra,
  });
  render(
    <MemoryRouter>
      <SessionProvider>
        <PermissionsProvider>
          <RequirePermission permission="source.read">
            <SourceManagement />
          </RequirePermission>
        </PermissionsProvider>
      </SessionProvider>
    </MemoryRouter>,
  );
  return api;
}

const card = (name: string) => screen.findByRole("article", { name: new RegExp(`^${name}`) });

describe("SourceManagement", () => {
  it("shows each connector's declared capabilities and what it does not support", async () => {
    renderSources(READ);
    const row = (await screen.findByText("tenant-csv", { selector: "strong" })).closest("tr")!;
    expect(within(row).getByText("files.upload@1.0.0")).toBeTruthy();
    expect(within(row).getByText("Supported: Discovery, Metadata, File ingestion")).toBeTruthy();
    expect(
      within(row).getByText(
        "Not supported: Batch extraction, Incremental, Events, Detail lookup",
      ),
    ).toBeTruthy();
    const paypal = screen.getByText("paypal", { selector: "strong" }).closest("tr")!;
    expect(within(paypal).getByText("Required")).toBeTruthy();
  });

  it("shows credential status, never a reference", async () => {
    renderSources(READ);
    const paypal = await card("PayPal Sandbox");
    expect(within(paypal).getByText("Required, not set")).toBeTruthy();
    const csv = await card("Bank statements");
    expect(within(csv).getByText("Set")).toBeTruthy();
    expect(within(csv).getByText("Not supported")).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/(env|managed):[A-Za-z0-9]/);
  });

  it("offers no action to a holder of the read keys alone", async () => {
    renderSources(READ);
    await card("PayPal Sandbox");
    expect(screen.queryByRole("heading", { name: "Register source" })).toBeNull();
    for (const name of ["Discover", "Sync", "Upload", "Store reference", "Register webhook"]) {
      expect(screen.queryByRole("button", { name })).toBeNull();
    }
  });

  it("shows a scoped action only on the source it is scoped to", async () => {
    renderSources([...READ, { key: "source.sync", scopes: [PAYPAL_SOURCE.id] }]);
    const paypal = await card("PayPal Sandbox");
    expect(within(paypal).getByRole("button", { name: "Sync" })).toBeTruthy();
    const csv = await card("Bank statements");
    expect(within(csv).queryByRole("button", { name: "Sync" })).toBeNull();
  });

  it("offers only the actions the connector supports", async () => {
    renderSources([...READ, "source.discover", "source.sync"]);
    const csv = await card("Bank statements");
    expect(within(csv).getByRole("button", { name: "Discover" })).toBeTruthy();
    expect(within(csv).queryByRole("button", { name: "Sync" })).toBeNull();
    const paypal = await card("PayPal Sandbox");
    expect(within(paypal).queryByRole("button", { name: "Discover" })).toBeNull();
  });

  it("registers a source without any credential field", async () => {
    const api = renderSources([...READ, "source.create"], {
      "POST /api/sources": { status: 201, body: sourceOf({ display_name: "Merchant DB" }) },
    });
    const form = (await screen.findByRole("heading", { name: "Register source" })).closest(
      "section",
    )!;
    fireEvent.change(within(form).getByLabelText("Connector"), {
      target: { value: "tenant-csv" },
    });
    fireEvent.change(within(form).getByLabelText("Display name"), {
      target: { value: "Merchant files" },
    });
    fireEvent.change(within(form).getByLabelText("Configuration (JSON)"), {
      target: { value: "[1]" },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Register source" }));
    expect(await within(form).findByText("Configuration must be a JSON object.")).toBeTruthy();
    expect(api.sent("POST /api/sources")).toEqual([]);

    fireEvent.change(within(form).getByLabelText("Configuration (JSON)"), {
      target: { value: '{"delimiter": ","}' },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Register source" }));
    await waitFor(() =>
      expect(api.sent("POST /api/sources")).toEqual([
        {
          connector_id: "tenant-csv",
          protocol: "csv",
          source_type: "tenant_file",
          display_name: "Merchant files",
          config: { delimiter: "," },
        },
      ]),
    );
    expect(await screen.findByRole("status")).toHaveProperty(
      "textContent",
      "Merchant files registered.",
    );
  });

  it("uploads a file with its kind and labels synthetic reference data", async () => {
    const api = renderSources([...READ, "source.file.upload"], {
      [`POST /api/sources/${CSV_SOURCE.id}/files`]: {
        status: 200,
        body: {
          source_id: CSV_SOURCE.id,
          evidence_id: "ev-1",
          object_name: "file:abc123",
          checksum: "abc123def456",
          provenance: "synthetic_reference",
          record_count: 12,
        },
      },
    });
    const csv = await card("Bank statements");
    const file = new File(["Transaction ID,Date\n"], "brr.csv", { type: "text/csv" });
    fireEvent.change(within(csv).getByLabelText("File"), { target: { files: [file] } });
    fireEvent.change(within(csv).getByLabelText("Kind"), { target: { value: "BRR" } });
    fireEvent.click(within(csv).getByRole("button", { name: "Upload" }));

    expect(await within(csv).findByText("SYNTHETIC/REFERENCE")).toBeTruthy();
    expect(within(csv).getByText("abc123def456")).toBeTruthy();
    const call = api.calls.find((item) => item.key === `POST /api/sources/${CSV_SOURCE.id}/files`)!;
    const body = call.init.body as FormData;
    expect(body.get("kind")).toBe("BRR");
    expect((body.get("file") as File).name).toBe("brr.csv");
    expect(call.init.headers).not.toHaveProperty("Content-Type");
  });

  it("stores a credential reference and clears the field", async () => {
    const api = renderSources([...READ, "source.credential.manage"], {
      [`PUT /api/sources/${PAYPAL_SOURCE.id}/credentials`]: [
        apiError(409, "SECRET_UNRESOLVED", "The source's secret reference cannot be resolved."),
        { status: 200, body: { ...PAYPAL_SOURCE, has_secret_reference: true } },
      ],
    });
    const paypal = await card("PayPal Sandbox");
    const input = within(paypal).getByLabelText("Secret reference") as HTMLInputElement;

    fireEvent.change(input, { target: { value: "env:MISSING" } });
    fireEvent.click(within(paypal).getByRole("button", { name: "Store reference" }));
    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      "The source's secret reference cannot be resolved.",
    );
    expect(input.value).toBe("env:MISSING");

    fireEvent.change(input, { target: { value: "env:PAYPAL_SANDBOX" } });
    fireEvent.click(within(paypal).getByRole("button", { name: "Store reference" }));
    await waitFor(() => expect(input.value).toBe(""));
    expect(api.sent(`PUT /api/sources/${PAYPAL_SOURCE.id}/credentials`)).toEqual([
      { secret_reference: "env:MISSING" },
      { secret_reference: "env:PAYPAL_SANDBOX" },
    ]);
  });

  it("connects a tenant's own PayPal app without keeping or showing the secret", async () => {
    const path = `PUT /api/sources/${PAYPAL_SOURCE.id}/credential-value`;
    const api = renderSources([...READ, "source.credential.manage"], {
      [path]: [
        apiError(422, "CREDENTIAL_REJECTED", "PayPal Sandbox did not accept these credentials."),
        { status: 200, body: { ...PAYPAL_SOURCE, has_secret_reference: true } },
      ],
    });
    const paypal = await card("PayPal Sandbox");
    const clientId = within(paypal).getByLabelText("Client ID") as HTMLInputElement;
    const secret = within(paypal).getByLabelText("Client secret") as HTMLInputElement;
    const connect = within(paypal).getByRole("button", { name: "Connect" });
    expect(secret.type).toBe("password");
    expect(connect).toHaveProperty("disabled", true);

    fireEvent.change(clientId, { target: { value: "AYclient" } });
    fireEvent.change(secret, { target: { value: "wrong" } });
    fireEvent.click(connect);
    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      "PayPal Sandbox did not accept these credentials.",
    );

    fireEvent.change(secret, { target: { value: "EKsecret" } });
    fireEvent.click(connect);
    await waitFor(() => expect(secret.value).toBe(""));
    expect(clientId.value).toBe("");
    expect(api.sent(path)).toEqual([
      { value: JSON.stringify({ client_id: "AYclient", client_secret: "wrong" }) },
      { value: JSON.stringify({ client_id: "AYclient", client_secret: "EKsecret" }) },
    ]);
  });

  it("offers no credential intake on a source that takes none", async () => {
    renderSources([...READ, "source.credential.manage"], {});
    const csv = await card("Bank statements");
    expect(within(csv).queryByLabelText("Credential")).toBeNull();
    expect(within(csv).queryByLabelText("Client secret")).toBeNull();
  });

  it("asks a database source for a managed reference, never a typed credential", async () => {
    const merchant: ConnectorManifest = {
      ...CSV,
      connector_id: "merchant-postgres",
      protocols: ["postgresql"],
      source_types: ["merchant_database"],
      auth_mode: "database_role",
      secret_reference_required: true,
    };
    const database = sourceOf({
      id: "s0000000-0000-4000-8000-000000000003",
      connector_id: "merchant-postgres",
      connector_version: "1.0.0",
      source_type: "merchant_database",
      protocol: "postgresql",
      display_name: "Merchant database",
      capabilities: [],
    });
    renderSources([...READ, "source.credential.manage"], {
      "GET /api/connectors": { status: 200, body: [PAYPAL, CSV, merchant] },
      "GET /api/sources": { status: 200, body: [PAYPAL_SOURCE, CSV_SOURCE, database] },
    });
    const db = await card("Merchant database");
    expect(within(db).queryByText("Connect your PayPal app")).toBeNull();
    expect(within(db).queryByLabelText("Client secret")).toBeNull();
    expect(within(db).getByText("Platform-managed reference")).toBeTruthy();
    expect(within(db).getByLabelText("Secret reference")).toBeTruthy();
    const paypal = await card("PayPal Sandbox");
    expect(within(paypal).getByText("Connect your PayPal app")).toBeTruthy();
    expect(within(paypal).getByText("Or use a platform-managed reference")).toBeTruthy();
  });

  it("registers a webhook with the checked event types only", async () => {
    const api = renderSources([...READ, "source.webhook.manage"], {
      [`PUT /api/sources/${PAYPAL_SOURCE.id}/webhooks`]: { status: 200, body: PAYPAL_SOURCE },
    });
    const csv = await card("Bank statements");
    expect(within(csv).queryByRole("button", { name: "Register webhook" })).toBeNull();

    const paypal = await card("PayPal Sandbox");
    const register = within(paypal).getByRole("button", { name: "Register webhook" });
    expect(register).toHaveProperty("disabled", true);
    fireEvent.change(within(paypal).getByLabelText("Provider webhook ID"), {
      target: { value: "WH-123" },
    });
    fireEvent.click(within(paypal).getByLabelText("PAYMENT.CAPTURE.COMPLETED"));
    fireEvent.click(register);
    await waitFor(() =>
      expect(api.sent(`PUT /api/sources/${PAYPAL_SOURCE.id}/webhooks`)).toEqual([
        { webhook_id: "WH-123", event_types: ["PAYMENT.CAPTURE.COMPLETED"] },
      ]),
    );
  });

  it("shows the server's message when sync is not available", async () => {
    renderSources([...READ, "source.sync"], {
      [`POST /api/sources/${PAYPAL_SOURCE.id}/sync`]: apiError(
        501,
        "NOT_IMPLEMENTED",
        "Not implemented yet (plan task 4.2).",
      ),
    });
    const paypal = await card("PayPal Sandbox");
    fireEvent.click(within(paypal).getByRole("button", { name: "Sync" }));
    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      "Not implemented yet (plan task 4.2).",
    );
  });
});

const EVENTS_PATH = `/api/sources/${PAYPAL_SOURCE.id}/webhook-events`;

const delivery = (overrides: Record<string, unknown>) => ({
  id: "e0000000-0000-4000-8000-000000000001",
  provider_event_id: "WH-EVT-1",
  event_type: "PAYMENT.CAPTURE.COMPLETED",
  status: "PROCESSED",
  attempts: 1,
  max_attempts: 3,
  last_error: null,
  provider_timestamp: "2026-11-11T10:00:00Z",
  received_at: "2026-11-11T10:00:01Z",
  processed_at: "2026-11-11T10:00:02Z",
  next_attempt_at: null,
  ...overrides,
});

const PROCESSED = delivery({});
const DEAD = delivery({
  id: "e0000000-0000-4000-8000-000000000002",
  provider_event_id: "WH-EVT-2",
  status: "DEAD_LETTER",
  attempts: 3,
  last_error: "evidence unreadable",
  processed_at: null,
});

const events = (body: unknown[]): Reply => ({ status: 200, body });

describe("Webhook events", () => {
  it("lists each delivery's state without offering replay to readers", async () => {
    renderSources(READ, { [`GET ${EVENTS_PATH}`]: events([PROCESSED, DEAD]) });
    const paypal = await card("PayPal Sandbox");
    const inbox = await within(paypal).findByRole("region", { name: "Webhook events" });
    const dead = (await within(inbox).findByText("WH-EVT-2")).closest("tr")!;
    expect(within(dead).getByText("DEAD_LETTER")).toBeTruthy();
    expect(within(dead).getByText("3 / 3")).toBeTruthy();
    expect(within(dead).getByText("evidence unreadable")).toBeTruthy();
    expect(within(inbox).getByText("PROCESSED")).toBeTruthy();
    expect(within(inbox).queryByRole("button", { name: /^Replay/ })).toBeNull();
  });

  it("offers replay only on failed or dead-lettered events, then re-reads the inbox", async () => {
    const replayed = { ...DEAD, status: "PROCESSED", attempts: 1, last_error: null };
    const scoped = { key: "source.webhook.manage", scopes: [PAYPAL_SOURCE.id] };
    const api = renderSources([...READ, scoped], {
      [`GET ${EVENTS_PATH}`]: [events([PROCESSED, DEAD]), events([PROCESSED, replayed])],
      [`POST ${EVENTS_PATH}/${DEAD.id}/replay`]: { status: 200, body: replayed },
    });
    const paypal = await card("PayPal Sandbox");
    const inbox = await within(paypal).findByRole("region", { name: "Webhook events" });
    const replay = await within(inbox).findByRole("button", { name: "Replay WH-EVT-2" });
    expect(within(inbox).queryByRole("button", { name: "Replay WH-EVT-1" })).toBeNull();

    fireEvent.click(replay);
    expect(await screen.findByText("PayPal Sandbox: WH-EVT-2 replayed.")).toBeTruthy();
    await waitFor(() => expect(within(inbox).queryByText("DEAD_LETTER")).toBeNull());
    const replays = api.calls.filter((call) => call.key === `POST ${EVENTS_PATH}/${DEAD.id}/replay`);
    expect(replays).toHaveLength(1);
    expect(within(inbox).queryByRole("button", { name: /^Replay/ })).toBeNull();
  });

  it("shows no inbox for a source that cannot receive webhooks", async () => {
    renderSources(READ, { [`GET ${EVENTS_PATH}`]: events([]) });
    const paypal = await card("PayPal Sandbox");
    expect(await within(paypal).findByText("No deliveries yet.")).toBeTruthy();
    const csv = await card("Bank statements");
    expect(within(csv).queryByRole("region", { name: "Webhook events" })).toBeNull();
  });
});