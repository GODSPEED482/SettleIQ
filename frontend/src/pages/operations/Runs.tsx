import { useEffect, useState } from "react";

import { messageOf } from "../../api/client";
import type { OperationsApi } from "./api";
import { StatusBadge, shortId } from "./labels";
import type { DomainRun } from "./types";

type Props = {
  api: OperationsApi;
  version: number;
  canStart: boolean;
  // The tenant's active packs, or null when they cannot be read.
  packs: string[] | null;
  busy: boolean;
  onStart: (namespace?: string) => void;
};

// Each run shows the versions it was pinned to, so its cases can be traced (spec 7, 3.4).
export function Runs({ api, version, canStart, packs, busy, onStart }: Props) {
  const [runs, setRuns] = useState<DomainRun[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    api
      .runs()
      .then((list) => current && setRuns(list))
      .catch((caught) => current && setError(messageOf(caught)));
    return () => {
      current = false;
    };
  }, [api, version]);

  return (
    <section className="panel">
      <h2>Runs</h2>
      {error && <p className="error" role="alert">{error}</p>}
      {canStart && (
        <p className="actions">
          {packs && packs.length > 1 ? (
            packs.map((namespace) => (
              <button key={namespace} type="button" disabled={busy} onClick={() => onStart(namespace)}>
                Run {namespace}
              </button>
            ))
          ) : (
            <button type="button" disabled={busy} onClick={() => onStart(packs?.[0])}>
              Start run
            </button>
          )}
        </p>
      )}
      {runs.length === 0 ? (
        <p className="muted">No runs yet.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Started</th>
                <th>Status</th>
                <th>Pack</th>
                <th>Ontology</th>
                <th>Rules</th>
                <th>Inputs</th>
                <th>Findings</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.id}>
                  <td>{new Date(run.started_at).toLocaleString()}</td>
                  <td>
                    <StatusBadge status={run.status} />
                    {run.failure && <div className="error">{run.failure}</div>}
                  </td>
                  <td>
                    {run.pack_namespace}@{run.pack_version}
                  </td>
                  <td>
                    v{run.ontology_version}{" "}
                    <code className="hash" title={run.ontology_content_hash}>
                      {run.ontology_content_hash.slice(0, 12)}
                    </code>
                  </td>
                  <td>{run.rule_version}</td>
                  <td>
                    {run.inputs_hash ? (
                      <code className="hash" title={run.inputs_hash}>
                        {run.inputs_hash.slice(0, 12)}
                      </code>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td title={shortId(run.id)}>{run.findings?.length ?? 0}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
