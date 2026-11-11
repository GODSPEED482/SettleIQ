import { useEffect, useMemo, useState } from "react";

import { messageOf } from "../../api/client";
import { can } from "../../permissions/can";
import { usePermissions } from "../../permissions/PermissionsContext";
import { sourcesApi } from "./api";
import { Connectors, RegisterSource } from "./Connectors";
import { SourceCard } from "./SourceCard";
import type { ConnectorManifest, CreateSourceInput, Source } from "./types";

// Source Management (spec 13.1): connectors and their declared capabilities, registered sources
// with credential and webhook status, the discover, sync, upload, credential and webhook
// actions, and each webhook source's inbox with replay. Each control shows only with its key
// from GET /api/me/permissions; the API rechecks every call.
export function SourceManagement() {
  const { tenantId, permissions } = usePermissions();
  const canReadConnectors = can(permissions, "connector.read");
  const canCreate = can(permissions, "source.create");

  const api = useMemo(() => (tenantId ? sourcesApi(tenantId) : null), [tenantId]);
  const [connectors, setConnectors] = useState<ConnectorManifest[]>([]);
  const [sources, setSources] = useState<Source[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    if (!api || !canReadConnectors) return;
    let current = true;
    api
      .connectors()
      .then((list) => current && setConnectors(list))
      .catch((caught) => current && setError(messageOf(caught)));
    return () => {
      current = false;
    };
  }, [api, canReadConnectors]);

  useEffect(() => {
    if (!api) return;
    let current = true;
    api
      .sources()
      .then((list) => current && setSources(list))
      .catch((caught) => current && setError(messageOf(caught)));
    return () => {
      current = false;
    };
  }, [api, version]);

  if (!api) return null;

  // Runs one change, reports it, then re-reads the sources.
  async function run<T>(work: () => Promise<T>, done: string): Promise<T | undefined> {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const value = await work();
      setNotice(done);
      return value;
    } catch (caught) {
      setError(messageOf(caught));
      return undefined;
    } finally {
      setBusy(false);
      setVersion((current) => current + 1);
    }
  }

  const create = async (body: CreateSourceInput) =>
    (await run(() => api.create(body), `${body.display_name} registered.`)) !== undefined;

  const manifests = new Map(connectors.map((item) => [item.connector_id, item]));

  return (
    <section className="sources">
      <h1>Sources</h1>
      <p className="muted">
        Connectors, registered sources and their credential and webhook status. Secret values
        never reach this screen; only whether a reference is stored. Reference data is labelled
        SYNTHETIC/REFERENCE wherever it appears.
      </p>
      {error && <p className="error" role="alert">{error}</p>}
      {notice && <p className="notice" role="status">{notice}</p>}
      {canReadConnectors && <Connectors connectors={connectors} />}
      {canCreate && canReadConnectors && connectors.length > 0 && (
        <RegisterSource connectors={connectors} busy={busy} onCreate={create} />
      )}
      <section className="panel">
        <h2>Registered sources</h2>
        {sources === null ? (
          <p className="muted">Loading…</p>
        ) : sources.length === 0 ? (
          <p className="muted">No sources registered yet.</p>
        ) : (
          sources.map((source) => (
            <SourceCard
              key={source.id}
              source={source}
              manifest={manifests.get(source.connector_id)}
              permissions={permissions}
              api={api}
              busy={busy}
              run={run}
            />
          ))
        )}
      </section>
    </section>
  );
}
