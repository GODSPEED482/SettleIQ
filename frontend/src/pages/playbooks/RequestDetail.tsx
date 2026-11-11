import { useEffect, useState, type FormEvent } from "react";

import { messageOf } from "../../api/client";
import { can, type EffectivePermission } from "../../permissions/can";
import { StatusBadge, shortId } from "../operations/labels";
import { canOnPlaybook, type PlaybooksApi } from "./api";
import { ModeLabel } from "./ModeLabel";
import { Facts, Proof } from "./Proof";
import {
  TERMINAL_EXECUTION,
  TRANSIENT_REQUEST,
  type AuditRecord,
  type Execution,
  type PlaybookRequest,
  type Readiness,
} from "./types";

const when = (value: string | null) => (value ? new Date(value).toLocaleString() : "—");

type Props = {
  api: PlaybooksApi;
  id: string;
  permissions: EffectivePermission[];
  readiness: Readiness[];
  pollMs: number;
  onChanged: () => void;
};

// One request with its lifecycle, both decision stages, the approval, the execution with its
// attempts and readback, and the audit records written for it (spec 9.3, 13.1).
export function RequestDetail({ api, id, permissions, readiness, pollMs, onChanged }: Props) {
  const [request, setRequest] = useState<PlaybookRequest | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    let current = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = () =>
      api
        .read(id)
        .then((found) => {
          if (!current) return;
          setRequest(found);
          if (TRANSIENT_REQUEST.includes(found.status)) timer = setTimeout(load, pollMs);
        })
        .catch((caught) => current && setError(messageOf(caught)));
    void load();
    return () => {
      current = false;
      clearTimeout(timer);
    };
  }, [api, id, pollMs, version]);

  if (error && !request) return <p className="error" role="alert">{error}</p>;
  if (!request) return null;

  const decided = () => {
    setVersion((current) => current + 1);
    onChanged();
  };

  return (
    <section className="panel request-detail" aria-label="Request detail">
      <h2>
        {request.playbook_id} <span className="muted">v{request.playbook_version}</span>{" "}
        <StatusBadge status={request.status} />
      </h2>
      <ModeLabel request={request} readiness={readiness} />
      <dl className="facts">
        <dt>Request</dt>
        <dd>{request.id}</dd>
        <dt>Case</dt>
        <dd>{request.case_id}</dd>
        <dt>Requested by</dt>
        <dd>{request.requested_by_me ? "You" : shortId(request.requested_by)}</dd>
        <dt>Action</dt>
        <dd>
          {request.connector_id} {request.action_capability}
        </dd>
        <dt>Pack version</dt>
        <dd>{request.pack_version}</dd>
        <dt>Created</dt>
        <dd>{when(request.created_at)}</dd>
      </dl>
      <section>
        <h3>Target</h3>
        <Facts values={request.target} />
      </section>
      <Proof request={request} />
      <Decisions request={request} />
      <ApprovalSection
        api={api}
        request={request}
        permissions={permissions}
        onDecided={decided}
      />
      {request.execution &&
        (can(permissions, "execution.read") ? (
          <ExecutionPanel api={api} id={request.execution.id} pollMs={pollMs} />
        ) : (
          <section>
            <h3>Execution</h3>
            <StatusBadge status={request.execution.status} />
          </section>
        ))}
      <AuditLinks api={api} request={request} canRead={can(permissions, "audit.read")} />
    </section>
  );
}

function Decisions({ request }: { request: PlaybookRequest }) {
  const blocked = request.decisions.some(
    (decision) => decision.stage === "EXECUTION" && decision.decision === "POLICY_BLOCKED",
  );
  return (
    <section>
      <h3>Policy decisions</h3>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Stage</th>
              <th>Decision</th>
              <th>Approval required</th>
              <th>Reason</th>
              <th>Policy</th>
              <th>Attestation</th>
              <th>Time</th>
            </tr>
          </thead>
          <tbody>
            {request.decisions.map((decision) => (
              <tr key={decision.id}>
                <td>{decision.stage}</td>
                <td>
                  <StatusBadge status={decision.decision} />
                </td>
                <td>{decision.approval_required ? "Yes" : "No"}</td>
                <td>{decision.reason ?? "—"}</td>
                <td>{decision.policy_version}</td>
                <td>{decision.attestation_id ? shortId(decision.attestation_id) : "—"}</td>
                <td>{when(decision.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {blocked && (
        <p className="muted">
          Blocked at the execution stage: the exact scope is in SHADOW or has no valid release
          attestation, so nothing was sent. Switching the scope to LIVE evaluates this request
          again.
        </p>
      )}
    </section>
  );
}

type ApprovalProps = {
  api: PlaybooksApi;
  request: PlaybookRequest;
  permissions: EffectivePermission[];
  onDecided: () => void;
};

// POL-01: the requester never sees approve or reject on their own request. The server refuses
// it anyway; hiding it only keeps the screen honest.
function ApprovalSection({ api, request, permissions, onDecided }: ApprovalProps) {
  const [reason, setReason] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const waiting = request.status === "NEEDS_APPROVAL";
  const canApprove = canOnPlaybook(permissions, "playbook.approve", request.playbook_id);
  const canReject = canOnPlaybook(permissions, "playbook.reject", request.playbook_id);
  const mayDecide = waiting && !request.requested_by_me && (canApprove || canReject);

  async function decide(event: FormEvent, outcome: "approve" | "reject") {
    event.preventDefault();
    setProblem(null);
    if (outcome === "reject" && !reason.trim()) {
      setProblem("Give a reason to reject.");
      return;
    }
    setBusy(true);
    try {
      await (outcome === "approve"
        ? api.approve(request.id, reason.trim())
        : api.reject(request.id, reason.trim()));
      onDecided();
    } catch (caught) {
      setProblem(messageOf(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <h3>Approval</h3>
      {request.approval ? (
        <dl className="facts">
          <dt>Decision</dt>
          <dd>
            <StatusBadge status={request.approval.decision} />
          </dd>
          <dt>Approver</dt>
          <dd>{shortId(request.approval.approver_user_id)}</dd>
          <dt>Reason</dt>
          <dd>{request.approval.reason ?? "—"}</dd>
          <dt>Time</dt>
          <dd>{when(request.approval.created_at)}</dd>
        </dl>
      ) : waiting ? (
        <p className="muted">
          {request.requested_by_me
            ? "You made this request, so another member must approve it."
            : "Waiting for an independent approver."}
        </p>
      ) : (
        <p className="muted">No approval recorded.</p>
      )}
      {mayDecide && (
        <form className="form decision" onSubmit={(event) => void decide(event, "approve")}>
          <label>
            Reason{canReject ? " (required to reject)" : ""}
            <input value={reason} onChange={(event) => setReason(event.target.value)} />
          </label>
          {problem && <p className="error" role="alert">{problem}</p>}
          <div className="actions">
            {canApprove && (
              <button type="submit" disabled={busy}>
                Approve
              </button>
            )}
            {canReject && (
              <button
                type="button"
                className="secondary"
                disabled={busy}
                onClick={(event) => void decide(event, "reject")}
              >
                Reject
              </button>
            )}
          </div>
        </form>
      )}
    </section>
  );
}

// The execution, re-read until it reaches a terminal state. A case resolves only on a matching
// readback, never on the provider's answer to the action itself (AC-PLY-06).
function ExecutionPanel({ api, id, pollMs }: { api: PlaybooksApi; id: string; pollMs: number }) {
  const [execution, setExecution] = useState<Execution | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = () =>
      api
        .execution(id)
        .then((found) => {
          if (!current) return;
          setExecution(found);
          if (!TERMINAL_EXECUTION.includes(found.status)) timer = setTimeout(load, pollMs);
        })
        .catch((caught) => current && setError(messageOf(caught)));
    void load();
    return () => {
      current = false;
      clearTimeout(timer);
    };
  }, [api, id, pollMs]);

  if (error) return <p className="error" role="alert">{error}</p>;
  if (!execution) return null;
  const verified = execution.status === "SUCCEEDED" && execution.result_ref;

  return (
    <section aria-label="Execution">
      <h3>
        Execution <StatusBadge status={execution.status} />
      </h3>
      <dl className="facts">
        <dt>Execution</dt>
        <dd>{execution.id}</dd>
        <dt>Idempotency key</dt>
        <dd>{execution.idempotency_key}</dd>
        <dt>Reason</dt>
        <dd>{execution.reason ?? "—"}</dd>
        <dt>Started</dt>
        <dd>{when(execution.started_at)}</dd>
        <dt>Completed</dt>
        <dd>{when(execution.completed_at)}</dd>
      </dl>
      <h4>Attempts</h4>
      {execution.attempts.length === 0 ? (
        <p className="muted">No attempts recorded.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>#</th>
                <th>Capability</th>
                <th>Status</th>
                <th>Error class</th>
                <th>Started</th>
              </tr>
            </thead>
            <tbody>
              {execution.attempts.map((attempt) => (
                <tr key={attempt.sequence}>
                  <td>{attempt.sequence}</td>
                  <td>{attempt.action_capability}</td>
                  <td>
                    <StatusBadge status={attempt.status} />
                  </td>
                  <td>{attempt.error_class ?? "—"}</td>
                  <td>{when(attempt.started_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <h4>Readback</h4>
      {verified ? (
        <>
          <p>
            Verified by {execution.result_ref?.readback} (evidence hash{" "}
            {execution.result_ref?.evidence_hash}). The case resolved only after this readback.
          </p>
          <pre className="trace-output" aria-label="Readback evidence">
            {JSON.stringify(execution.result_ref?.evidence, null, 2)}
          </pre>
        </>
      ) : TERMINAL_EXECUTION.includes(execution.status) ? (
        <p className="muted">Not verified. The case went back to human review.</p>
      ) : (
        <p className="notice" role="status">
          Waiting for the Worker. This view refreshes until the execution finishes.
        </p>
      )}
    </section>
  );
}

type AuditProps = { api: PlaybooksApi; request: PlaybookRequest; canRead: boolean };

function AuditLinks({ api, request, canRead }: AuditProps) {
  const [open, setOpen] = useState<AuditRecord | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function show(id: string) {
    setError(null);
    try {
      setOpen(open?.id === id ? null : await api.audit(id));
    } catch (caught) {
      setError(messageOf(caught));
    }
  }

  return (
    <section>
      <h3>Audit</h3>
      {request.audit_refs.length === 0 ? (
        <p className="muted">No audit records yet.</p>
      ) : (
        <ul className="audit-refs">
          {request.audit_refs.map((ref) => (
            <li key={ref.id}>
              {ref.action} {ref.decision && <StatusBadge status={ref.decision} />}{" "}
              <span className="muted">{when(ref.created_at)}</span>{" "}
              {canRead && (
                <button
                  type="button"
                  className="secondary"
                  aria-label={`Open audit ${ref.action} ${ref.id}`}
                  onClick={() => void show(ref.id)}
                >
                  {open?.id === ref.id ? "Hide" : "Open"}
                </button>
              )}
              {open?.id === ref.id && (
                <pre className="trace-output" aria-label="Audit record">
                  {JSON.stringify(open, null, 2)}
                </pre>
              )}
            </li>
          ))}
        </ul>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  );
}
