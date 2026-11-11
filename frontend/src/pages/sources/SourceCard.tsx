import { useEffect, useId, useState, type FormEvent } from "react";

import { messageOf } from "../../api/client";
import { can, type EffectivePermission } from "../../permissions/can";
import { ProvenanceLabel, StatusBadge } from "../operations/labels";
import { webhookEventTypes, type SourcesApi } from "./api";
import {
  REPLAYABLE,
  type ConnectorManifest,
  type DiscoverResult,
  type FileKind,
  type Source,
  type SyncResult,
  type UploadResult,
  type WebhookEvent,
} from "./types";

type Result =
  | { kind: "discover"; value: DiscoverResult }
  | { kind: "sync"; value: SyncResult }
  | { kind: "upload"; value: UploadResult };

type Props = {
  source: Source;
  // Absent without connector.read: actions then show by permission alone, and the API refuses
  // what the connector does not support.
  manifest?: ConnectorManifest;
  permissions: EffectivePermission[];
  api: SourcesApi;
  busy: boolean;
  run: <T>(work: () => Promise<T>, done: string) => Promise<T | undefined>;
};

// One registered source. Each action needs its key for this source, tenant-wide or scoped to it.
export function SourceCard({ source, manifest, permissions, api, busy, run }: Props) {
  const headingId = useId();
  const [result, setResult] = useState<Result | null>(null);
  const scope = { type: "source", id: source.id };
  const allowed = (key: string) => can(permissions, key, scope);
  const supports = (flag: boolean | undefined) => flag ?? true;
  const eventTypes = webhookEventTypes(source);

  const canDiscover = allowed("source.discover") && supports(manifest?.supports_discovery);
  const canSync = allowed("source.sync") && supports(manifest?.supports_batch_extraction);
  const canUpload = allowed("source.file.upload") && supports(manifest?.supports_file_ingestion);
  const canCredential = allowed("source.credential.manage");
  const takesCredential = manifest?.secret_reference_required ?? true;
  const canWebhook = allowed("source.webhook.manage") && eventTypes !== null;

  async function discover() {
    const value = await run(() => api.discover(source.id), `${source.display_name}: discovery finished.`);
    if (value) setResult({ kind: "discover", value });
  }

  async function sync() {
    const value = await run(() => api.sync(source.id), `${source.display_name}: sync finished.`);
    if (value) setResult({ kind: "sync", value });
  }

  return (
    <article className="source-card" aria-labelledby={headingId}>
      <h3 id={headingId}>
        {source.display_name} <StatusBadge status={source.status} />
      </h3>
      <dl className="facts">
        <dt>Connector</dt>
        <dd>
          {source.connector_id}@{source.connector_version}
        </dd>
        <dt>Binding</dt>
        <dd>
          {source.protocol} / {source.source_type}
        </dd>
        <dt>Credential</dt>
        <dd>{credentialStatus(source, manifest)}</dd>
        <dt>Webhook</dt>
        <dd>
          {eventTypes === null
            ? "Not supported"
            : source.config.webhook
              ? `${source.config.webhook.webhook_id} (${source.config.webhook.event_types.join(", ")})`
              : "Not registered"}
        </dd>
        <dt>Capabilities</dt>
        <dd>
          {source.capabilities.length === 0 ? (
            <span className="muted">None</span>
          ) : (
            <ul className="chips">
              {source.capabilities.map((capability) => (
                <li key={capability.key} className="chip">
                  {capability.key}@{capability.version}
                </li>
              ))}
            </ul>
          )}
        </dd>
      </dl>
      {(canDiscover || canSync) && (
        <div className="actions">
          {canDiscover && (
            <button type="button" disabled={busy} onClick={() => void discover()}>
              Discover
            </button>
          )}
          {canSync && (
            <button type="button" disabled={busy} onClick={() => void sync()}>
              Sync
            </button>
          )}
        </div>
      )}
      {canUpload && (
        <UploadForm
          busy={busy}
          onUpload={async (file, kind) => {
            const value = await run(
              () => api.upload(source.id, file, kind),
              `${source.display_name}: ${file.name} ingested.`,
            );
            if (value) setResult({ kind: "upload", value });
            return value !== undefined;
          }}
        />
      )}
      {canCredential && takesCredential && (
        <ConnectForm
          busy={busy}
          paypal={source.connector_id === "paypal"}
          replacing={source.has_secret_reference}
          onConnect={async (value) =>
            (await run(
              () => api.connectCredential(source.id, value),
              `${source.display_name}: credentials connected.`,
            )) !== undefined
          }
        />
      )}
      {canCredential && (
        <CredentialForm
          busy={busy}
          rotating={source.has_secret_reference}
          onStore={async (reference) =>
            (await run(
              () => api.setCredential(source.id, reference),
              `${source.display_name}: credential reference stored.`,
            )) !== undefined
          }
        />
      )}
      {canWebhook && (
        <WebhookForm
          busy={busy}
          eventTypes={eventTypes}
          current={source.config.webhook}
          onRegister={async (webhookId, types) =>
            (await run(
              () => api.registerWebhook(source.id, { webhook_id: webhookId, event_types: types }),
              `${source.display_name}: webhook registered.`,
            )) !== undefined
          }
        />
      )}
      {result && <ResultView result={result} />}
      {eventTypes !== null && (
        <WebhookEvents
          source={source}
          api={api}
          canReplay={allowed("source.webhook.manage")}
          busy={busy}
          run={run}
        />
      )}
    </article>
  );
}

// The source's webhook inbox: what arrived, how processing went, and replay of failed and
// dead-lettered events (spec 10.4). Readable with source.read; replay needs the webhook key.
function WebhookEvents({
  source,
  api,
  canReplay,
  busy,
  run,
}: Pick<Props, "source" | "api" | "busy" | "run"> & { canReplay: boolean }) {
  const headingId = useId();
  const [events, setEvents] = useState<WebhookEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    let current = true;
    api
      .webhookEvents(source.id)
      .then((list) => {
        if (!current) return;
        setEvents(list);
        setError(null);
      })
      .catch((caught) => current && setError(messageOf(caught)));
    return () => {
      current = false;
    };
  }, [api, source.id, version]);

  async function replay(item: WebhookEvent) {
    await run(
      () => api.replayWebhook(source.id, item.id),
      `${source.display_name}: ${item.provider_event_id} replayed.`,
    );
    setVersion((current) => current + 1);
  }

  return (
    <section className="webhook-events" aria-labelledby={headingId}>
      <div className="row">
        <h4 id={headingId}>Webhook events</h4>
        <button type="button" disabled={busy} onClick={() => setVersion((current) => current + 1)}>
          Refresh
        </button>
      </div>
      {error ? (
        <p className="error">{error}</p>
      ) : events === null ? (
        <p className="muted">Loading…</p>
      ) : events.length === 0 ? (
        <p className="muted">No deliveries yet.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Event</th>
                <th>Type</th>
                <th>Status</th>
                <th>Attempts</th>
                <th>Received</th>
                <th>Last error</th>
                {canReplay && <th aria-label="Actions" />}
              </tr>
            </thead>
            <tbody>
              {events.map((item) => (
                <tr key={item.id}>
                  <td>
                    <code>{item.provider_event_id}</code>
                  </td>
                  <td>{item.event_type}</td>
                  <td>
                    <StatusBadge status={item.status} />
                    {item.status === "FAILED" && item.next_attempt_at && (
                      <span className="muted">
                        {" "}
                        retry after {new Date(item.next_attempt_at).toLocaleString()}
                      </span>
                    )}
                  </td>
                  <td>
                    {item.attempts} / {item.max_attempts}
                  </td>
                  <td>{new Date(item.received_at).toLocaleString()}</td>
                  <td>{item.last_error ?? <span className="muted">—</span>}</td>
                  {canReplay && (
                    <td>
                      {REPLAYABLE.includes(item.status) && (
                        <button
                          type="button"
                          disabled={busy}
                          aria-label={`Replay ${item.provider_event_id}`}
                          onClick={() => void replay(item)}
                        >
                          Replay
                        </button>
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// Whether a reference is stored, never the reference or the value (spec 11.3).
function credentialStatus(source: Source, manifest?: ConnectorManifest): string {
  if (source.has_secret_reference) return "Set";
  return manifest?.secret_reference_required ? "Required, not set" : "Not set";
}

function UploadForm({
  busy,
  onUpload,
}: {
  busy: boolean;
  onUpload: (file: File, kind: FileKind) => Promise<boolean>;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [kind, setKind] = useState<FileKind>("BANK_CSV");
  const [inputKey, setInputKey] = useState(0);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!file) return;
    if (await onUpload(file, kind)) {
      setFile(null);
      setInputKey((current) => current + 1);
    }
  }

  return (
    <form className="form inline" onSubmit={(event) => void submit(event)}>
      <h4>Upload file</h4>
      <label>
        File
        <input
          key={inputKey}
          type="file"
          accept=".csv,text/csv"
          onChange={(event) => setFile(event.target.files?.[0] ?? null)}
        />
      </label>
      <label>
        Kind
        <select value={kind} onChange={(event) => setKind(event.target.value as FileKind)}>
          <option value="BANK_CSV">Bank CSV</option>
          <option value="BRR">Balance Reconciliation Report</option>
        </select>
      </label>
      <div className="actions">
        <button type="submit" disabled={busy || !file}>
          Upload
        </button>
      </div>
    </form>
  );
}

// Write-only intake (spec v1.7 §11.3, AC-PP-14): the tenant's own credentials go to the server
// once, sealed there, and are never shown again. The fields clear once they are accepted.
function ConnectForm({
  busy,
  paypal,
  replacing,
  onConnect,
}: {
  busy: boolean;
  paypal: boolean;
  replacing: boolean;
  onConnect: (value: string) => Promise<boolean>;
}) {
  const [clientId, setClientId] = useState("");
  const [secret, setSecret] = useState("");
  const hintId = useId();
  const ready = secret.trim() !== "" && (!paypal || clientId.trim() !== "");

  async function submit(event: FormEvent) {
    event.preventDefault();
    const value = paypal
      ? JSON.stringify({ client_id: clientId.trim(), client_secret: secret.trim() })
      : secret.trim();
    if (await onConnect(value)) {
      setClientId("");
      setSecret("");
    }
  }

  return (
    <form className="form inline" onSubmit={(event) => void submit(event)}>
      <h4>{paypal ? "Connect your PayPal app" : "Connect credentials"}</h4>
      {paypal && (
        <label>
          Client ID
          <input
            value={clientId}
            autoComplete="off"
            spellCheck={false}
            maxLength={500}
            onChange={(event) => setClientId(event.target.value)}
          />
        </label>
      )}
      <label>
        {paypal ? "Client secret" : "Credential"}
        <input
          type="password"
          value={secret}
          autoComplete="new-password"
          spellCheck={false}
          maxLength={3000}
          aria-describedby={hintId}
          onChange={(event) => setSecret(event.target.value)}
        />
      </label>
      <span id={hintId} className="hint">
        {paypal
          ? "From a dedicated REST app in your own PayPal Sandbox account, with Transaction search and Accept payments enabled. "
          : ""}
        Sealed on the server and never shown again.
      </span>
      <div className="actions">
        <button type="submit" disabled={busy || !ready}>
          {replacing ? "Replace credentials" : "Connect"}
        </button>
      </div>
    </form>
  );
}

// Write-only: the field is empty on every render and cleared once the reference is stored.
function CredentialForm({
  busy,
  rotating,
  onStore,
}: {
  busy: boolean;
  rotating: boolean;
  onStore: (reference: string) => Promise<boolean>;
}) {
  const [reference, setReference] = useState("");
  const hintId = useId();

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (await onStore(reference.trim())) setReference("");
  }

  return (
    <form className="form inline" onSubmit={(event) => void submit(event)}>
      <h4>{rotating ? "Rotate credential reference" : "Or use a platform-managed reference"}</h4>
      <label>
        Secret reference
        <input
          value={reference}
          autoComplete="off"
          spellCheck={false}
          maxLength={500}
          aria-describedby={hintId}
          onChange={(event) => setReference(event.target.value)}
        />
      </label>
      <span id={hintId} className="hint">
        A reference such as <code>env:…</code> or <code>managed:…</code>, never the secret itself.
      </span>
      <div className="actions">
        <button type="submit" disabled={busy || !reference.trim()}>
          {rotating ? "Rotate reference" : "Store reference"}
        </button>
      </div>
    </form>
  );
}

function WebhookForm({
  busy,
  eventTypes,
  current,
  onRegister,
}: {
  busy: boolean;
  eventTypes: string[];
  current?: { webhook_id: string; event_types: string[] };
  onRegister: (webhookId: string, types: string[]) => Promise<boolean>;
}) {
  const [webhookId, setWebhookId] = useState(current?.webhook_id ?? "");
  const [checked, setChecked] = useState<string[]>(current?.event_types ?? []);

  function toggle(value: string) {
    setChecked((list) =>
      list.includes(value) ? list.filter((item) => item !== value) : [...list, value],
    );
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    // Keep the contract's order, so a re-registration with the same choice is unchanged.
    await onRegister(
      webhookId.trim(),
      eventTypes.filter((value) => checked.includes(value)),
    );
  }

  return (
    <form className="form inline" onSubmit={(event) => void submit(event)}>
      <h4>Webhook registration</h4>
      <label>
        Provider webhook ID
        <input
          value={webhookId}
          maxLength={100}
          onChange={(event) => setWebhookId(event.target.value)}
        />
      </label>
      <fieldset className="event-types">
        <legend>Event types</legend>
        {eventTypes.map((value) => (
          <label key={value} className="check">
            <input
              type="checkbox"
              checked={checked.includes(value)}
              onChange={() => toggle(value)}
            />
            {value}
          </label>
        ))}
      </fieldset>
      <div className="actions">
        <button type="submit" disabled={busy || !webhookId.trim() || checked.length === 0}>
          Register webhook
        </button>
      </div>
    </form>
  );
}

function ResultView({ result }: { result: Result }) {
  if (result.kind === "sync") {
    return <p className="notice">Synced {result.value.record_count} records.</p>;
  }
  if (result.kind === "upload") {
    const { value } = result;
    return (
      <div className="notice">
        <p>
          {value.object_name}: {value.record_count} records{" "}
          <ProvenanceLabel provenance={value.provenance} />
        </p>
        <p className="muted">
          Checksum <code className="hash">{value.checksum}</code>
        </p>
      </div>
    );
  }
  const { objects } = result.value;
  return (
    <div className="notice">
      {objects.length === 0 ? (
        <p>Nothing discovered.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Object</th>
                <th>Type</th>
                <th>Rows</th>
                <th>Columns</th>
              </tr>
            </thead>
            <tbody>
              {objects.map((item) => (
                <tr key={item.object_name}>
                  <td>{item.object_name}</td>
                  <td>{item.object_type}</td>
                  <td>{item.row_count ?? "—"}</td>
                  <td>{item.columns}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
