import { useState } from "react";

import { messageOf } from "../../api/client";
import { ConfirmButton } from "../team/ConfirmButton";
import { environmentOf, type PlaybooksApi } from "./api";
import type { ModeChange, Readiness } from "./types";

type Props = {
  api: PlaybooksApi;
  readiness: Readiness[];
  canManage: boolean;
  onChanged: (change: ModeChange) => void;
};

const scopeKey = (scope: Readiness) =>
  `${scope.domain_pack_id}/${scope.connector_id}/${scope.action_capability}`;

function summary(change: ModeChange): string {
  const done = `${change.action_capability} is ${change.mode}`;
  const count = change.reevaluated_requests?.length ?? 0;
  if (change.mode !== "LIVE") return `${done}.`;
  if (count === 0) return `${done}; no blocked requests were waiting.`;
  return `${done}; ${count} blocked ${count === 1 ? "request was" : "requests were"} evaluated again.`;
}

// Each exact scope's SettleIQ mode beside its provider environment (spec 9.5). A holder of
// execution_mode.manage switches one scope at a time; the server audits the switch and, on LIVE,
// evaluates again the requests that scope blocked.
export function ExecutionModes({ api, readiness, canManage, onChanged }: Props) {
  const [busy, setBusy] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const switchMode = (scope: Readiness) => {
    const mode = scope.mode === "LIVE" ? "SHADOW" : "LIVE";
    setBusy(scopeKey(scope));
    setDone(null);
    setError(null);
    api
      .setMode(scope, mode)
      .then((change) => {
        setDone(summary(change));
        onChanged(change);
      })
      .catch((caught) => setError(messageOf(caught)))
      .finally(() => setBusy(null));
  };

  return (
    <section className="panel" aria-label="Execution mode">
      <h2>Execution mode</h2>
      <p className="muted">
        Every scope starts in SHADOW, where nothing that changes business state runs. Switching one
        scope never changes another, and a LIVE scope still needs an approved request, revalidated
        preconditions and a single-use grant.
      </p>
      {!canManage && <p className="muted">Only the tenant owner can switch a scope.</p>}
      {done && <p role="status">{done}</p>}
      {error && <p className="error" role="alert">{error}</p>}
      {readiness.length === 0 ? (
        <p className="muted">No active pack declares a state-changing action.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Domain pack</th>
                <th>Connector</th>
                <th>Action</th>
                <th>Mode</th>
                {canManage && <th />}
              </tr>
            </thead>
            <tbody>
              {readiness.map((scope) => (
                <tr key={scopeKey(scope)}>
                  <td>
                    {scope.domain_pack_id} {scope.domain_pack_version}
                  </td>
                  <td>{environmentOf(scope.connector_id)}</td>
                  <td>{scope.action_capability}</td>
                  <td>
                    <span className={`status status-${scope.mode.toLowerCase()}`}>{scope.mode}</span>
                  </td>
                  {canManage && (
                    <td>
                      <ConfirmButton
                        label={scope.mode === "LIVE" ? "Return to SHADOW" : "Switch to LIVE"}
                        disabled={busy !== null}
                        onConfirm={() => switchMode(scope)}
                      />
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
