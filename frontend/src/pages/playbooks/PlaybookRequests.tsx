import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { messageOf } from "../../api/client";
import { can } from "../../permissions/can";
import { usePermissions } from "../../permissions/PermissionsContext";
import { StatusBadge, shortId } from "../operations/labels";
import { playbooksApi } from "./api";
import { ExecutionModes } from "./ExecutionModes";
import { ModeLabel } from "./ModeLabel";
import { RequestDetail } from "./RequestDetail";
import type { PlaybookRequest, Readiness, RequestStatus } from "./types";

export const POLL_MS = 2000;

const STATUSES: RequestStatus[] = [
  "NEEDS_APPROVAL",
  "APPROVED",
  "PRECONDITIONS_VALIDATED",
  "EXECUTION_CREATED",
  "DENIED",
  "REJECTED",
  "EXPIRED",
  "CANCELLED",
];

// Playbook Requests (spec 9, 13.1). Requests are made from a case in Domain Operations; here
// they are reviewed, approved or rejected, and followed through execution and readback. The
// list holds only the playbooks the reader may see; the API rechecks every call.
export function PlaybookRequests({ pollMs = POLL_MS }: { pollMs?: number }) {
  const { tenantId, permissions } = usePermissions();
  const canReadExecutions = can(permissions, "execution.read");
  const canManageModes = can(permissions, "execution_mode.manage");
  const api = useMemo(() => (tenantId ? playbooksApi(tenantId) : null), [tenantId]);
  const [params, setParams] = useSearchParams();
  const selected = params.get("request");
  const [requests, setRequests] = useState<PlaybookRequest[]>([]);
  const [readiness, setReadiness] = useState<Readiness[]>([]);
  const [status, setStatus] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    if (!api) return;
    let current = true;
    api
      .list({ status })
      .then((list) => current && setRequests(list))
      .catch((caught) => current && setError(messageOf(caught)));
    return () => {
      current = false;
    };
  }, [api, status, version]);

  // Each exact scope's mode comes from the execution readiness read (execution.read).
  useEffect(() => {
    if (!api || !canReadExecutions) return;
    let current = true;
    api
      .readiness()
      .then((rows) => current && setReadiness(rows))
      .catch((caught) => current && setError(messageOf(caught)));
    return () => {
      current = false;
    };
  }, [api, canReadExecutions, version]);

  if (!api) return null;

  const open = (id: string | null) => setParams(id ? { request: id } : {});

  return (
    <section className="playbooks">
      <h1>Playbook Requests</h1>
      <p className="muted">
        Each request carries its REQUEST- and EXECUTION-stage decisions, any approval, and the
        execution with its attempts and readback. Request a playbook from a case in Domain
        Operations.
      </p>
      {error && <p className="error" role="alert">{error}</p>}
      {canReadExecutions && (
        <ExecutionModes
          api={api}
          readiness={readiness}
          canManage={canManageModes}
          onChanged={() => setVersion((current) => current + 1)}
        />
      )}
      <section className="panel">
        <h2>Requests</h2>
        <div className="form inline filters">
          <label>
            Status
            <select value={status} onChange={(event) => setStatus(event.target.value)}>
              <option value="">Any</option>
              {STATUSES.map((value) => (
                <option key={value}>{value}</option>
              ))}
            </select>
          </label>
        </div>
        {requests.length === 0 ? (
          <p className="muted">No requests match.</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Playbook</th>
                  <th>Case</th>
                  <th>Status</th>
                  <th>Mode</th>
                  <th>Requested by</th>
                  <th>Created</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {requests.map((item) => (
                  <tr key={item.id}>
                    <td>{item.playbook_id}</td>
                    <td>{shortId(item.case_id)}</td>
                    <td>
                      <StatusBadge status={item.status} />
                    </td>
                    <td>
                      <ModeLabel request={item} readiness={readiness} />
                    </td>
                    <td>{item.requested_by_me ? "You" : shortId(item.requested_by)}</td>
                    <td>{new Date(item.created_at).toLocaleString()}</td>
                    <td>
                      <button
                        type="button"
                        className="secondary"
                        aria-label={`Open request ${item.id}`}
                        onClick={() => open(item.id === selected ? null : item.id)}
                      >
                        {item.id === selected ? "Close" : "Open"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      {selected && (
        <RequestDetail
          key={selected}
          api={api}
          id={selected}
          permissions={permissions}
          readiness={readiness}
          pollMs={pollMs}
          onChanged={() => setVersion((current) => current + 1)}
        />
      )}
    </section>
  );
}
