import { useId, useState, type FormEvent } from "react";

import { SUPPORT_FLAGS, type ConnectorManifest, type CreateSourceInput } from "./types";

// Each connector's declared contract (spec 4.4): what it supports, what it states it does not,
// and the versioned capability keys a source snapshots when it is registered.
export function Connectors({ connectors }: { connectors: ConnectorManifest[] }) {
  return (
    <section className="panel">
      <h2>Connectors</h2>
      {connectors.length === 0 ? (
        <p className="muted">No connectors are installed.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Connector</th>
                <th>Binding</th>
                <th>Credential</th>
                <th>Supports</th>
                <th>Capabilities</th>
              </tr>
            </thead>
            <tbody>
              {connectors.map((connector) => {
                const supported = SUPPORT_FLAGS.filter(([flag]) => connector[flag]);
                const unsupported = SUPPORT_FLAGS.filter(([flag]) => !connector[flag]);
                return (
                  <tr key={connector.connector_id}>
                    <td>
                      <strong>{connector.connector_id}</strong>
                      <div className="muted">v{connector.version}</div>
                    </td>
                    <td>
                      {connector.protocols.join(", ")}
                      <div className="muted">{connector.source_types.join(", ")}</div>
                    </td>
                    <td>
                      {connector.secret_reference_required ? "Required" : "Not required"}
                      <div className="muted">{connector.auth_mode}</div>
                    </td>
                    <td>
                      <div>Supported: {names(supported)}</div>
                      <div className="muted">Not supported: {names(unsupported)}</div>
                    </td>
                    <td>
                      {connector.capabilities.length === 0 ? (
                        <span className="muted">None declared</span>
                      ) : (
                        <ul className="chips">
                          {connector.capabilities.map((capability) => (
                            <li key={capability.key} className="chip">
                              {capability.key}@{capability.version}
                            </li>
                          ))}
                        </ul>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

const names = (flags: [string, string][]) =>
  flags.length ? flags.map(([, label]) => label).join(", ") : "none";

type RegisterProps = {
  connectors: ConnectorManifest[];
  busy: boolean;
  onCreate: (body: CreateSourceInput) => Promise<boolean>;
};

// Registration binds one declared protocol and source type. Credentials are never set here:
// they go through the separate credential route, which needs its own critical key (AC-PP-10).
export function RegisterSource({ connectors, busy, onCreate }: RegisterProps) {
  const [connectorId, setConnectorId] = useState(connectors[0]?.connector_id ?? "");
  const connector = connectors.find((item) => item.connector_id === connectorId) ?? connectors[0];
  const [protocol, setProtocol] = useState(connector?.protocols[0] ?? "");
  const [sourceType, setSourceType] = useState(connector?.source_types[0] ?? "");
  const [name, setName] = useState("");
  const [config, setConfig] = useState("{}");
  const [error, setError] = useState<string | null>(null);
  const hintId = useId();

  if (!connector) return null;

  function choose(id: string) {
    const next = connectors.find((item) => item.connector_id === id);
    setConnectorId(id);
    setProtocol(next?.protocols[0] ?? "");
    setSourceType(next?.source_types[0] ?? "");
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const parsed = parseConfig(config);
    if (parsed === null) {
      setError("Configuration must be a JSON object.");
      return;
    }
    setError(null);
    const created = await onCreate({
      connector_id: connector.connector_id,
      protocol,
      source_type: sourceType,
      display_name: name.trim(),
      config: parsed,
    });
    if (created) {
      setName("");
      setConfig("{}");
    }
  }

  return (
    <section className="panel">
      <h2>Register source</h2>
      <form className="form narrow" onSubmit={(event) => void submit(event)}>
        <label>
          Connector
          <select value={connector.connector_id} onChange={(event) => choose(event.target.value)}>
            {connectors.map((item) => (
              <option key={item.connector_id} value={item.connector_id}>
                {item.connector_id}
              </option>
            ))}
          </select>
        </label>
        <label>
          Protocol
          <select value={protocol} onChange={(event) => setProtocol(event.target.value)}>
            {connector.protocols.map((value) => (
              <option key={value}>{value}</option>
            ))}
          </select>
        </label>
        <label>
          Source type
          <select value={sourceType} onChange={(event) => setSourceType(event.target.value)}>
            {connector.source_types.map((value) => (
              <option key={value}>{value}</option>
            ))}
          </select>
        </label>
        <label>
          Display name
          <input
            value={name}
            required
            maxLength={200}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        <label>
          Configuration (JSON)
          <textarea
            rows={4}
            value={config}
            spellCheck={false}
            aria-describedby={hintId}
            onChange={(event) => setConfig(event.target.value)}
          />
        </label>
        <span id={hintId} className="hint">
          Settings only, never secrets. Store the credential reference after registering.
        </span>
        {error && <p className="error" role="alert">{error}</p>}
        <div className="actions">
          <button type="submit" disabled={busy || !name.trim()}>
            Register source
          </button>
        </div>
      </form>
    </section>
  );
}

function parseConfig(text: string): Record<string, unknown> | null {
  try {
    const value: unknown = JSON.parse(text.trim() || "{}");
    return value && typeof value === "object" && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}
