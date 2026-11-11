import { useId, useState, type FormEvent } from "react";

import { can, type EffectivePermission } from "../../permissions/can";
import { ProvenanceLabel, StatusBadge } from "../operations/labels";
import { webhookEventTypes, type SourcesApi } from "./api";
import type {
  ConnectorManifest,
  DiscoverResult,
  FileKind,
  Source,
  SyncResult,
  UploadResult,
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
    </article>
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
      <h4>{rotating ? "Rotate credential reference" : "Store credential reference"}</h4>
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
