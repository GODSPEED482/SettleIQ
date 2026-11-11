import { useEffect, useMemo, useState, type FormEvent } from "react";

import { messageOf } from "../../api/client";
import { can } from "../../permissions/can";
import { usePermissions } from "../../permissions/PermissionsContext";
import { StatusBadge, shortId } from "../operations/labels";
import type { CaseStatus, DomainCase } from "../operations/types";
import { investigationsApi, type InvestigationsApi } from "./api";
import type { Investigation, InvestigationSummary, ToolCall } from "./types";

export const POLL_MS = 2000;

// Cases the agent may still be asked about; a resolved or closed case has nothing to explain.
const INVESTIGABLE: CaseStatus[] = ["OPEN", "INVESTIGATING", "HUMAN_REVIEW"];

const when = (value: string | null) => (value ? new Date(value).toLocaleString() : "—");

// Investigation (spec 8, 13.1): start a delegated investigation of a case and read its validated
// result and tool trace. The agent reads, recommends and validates only, so nothing on this
// screen creates a request, an approval or an execution.
export function Investigations({ pollMs = POLL_MS }: { pollMs?: number }) {
  const { tenantId, permissions } = usePermissions();
  const canStart = can(permissions, "case.investigate");
  const canReadCases = can(permissions, "case.read");
  const api = useMemo(() => (tenantId ? investigationsApi(tenantId) : null), [tenantId]);
  const [items, setItems] = useState<InvestigationSummary[]>([]);
  const [cases, setCases] = useState<DomainCase[]>([]);
  const [caseId, setCaseId] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    if (!api) return;
    let current = true;
    api
      .list()
      .then((list) => current && setItems(list))
      .catch((caught) => current && setError(messageOf(caught)));
    return () => {
      current = false;
    };
  }, [api, version]);

  // Choosing a case to investigate needs case.read as well as case.investigate.
  useEffect(() => {
    if (!api || !canStart || !canReadCases) return;
    let current = true;
    api
      .cases()
      .then((list) => current && setCases(list.filter((item) => INVESTIGABLE.includes(item.status))))
      .catch((caught) => current && setError(messageOf(caught)));
    return () => {
      current = false;
    };
  }, [api, canStart, canReadCases, version]);

  if (!api) return null;

  const caseLabel = (id: string) => {
    const found = cases.find((item) => item.id === id);
    return found ? `${found.case_type} ${shortId(id)}` : shortId(id);
  };

  async function start(event: FormEvent) {
    event.preventDefault();
    if (!api || !caseId) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const started = await api.start(caseId);
      setSelected(started.investigation_id);
      setNotice("Investigation started.");
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy(false);
      setVersion((current) => current + 1);
    }
  }

  return (
    <section className="investigations">
      <h1>Investigation</h1>
      <p className="muted">
        The agent reads a case's evidence through its registered tools and recommends a playbook.
        It never creates a request, an approval or an execution: request a playbook from the
        case in Domain Operations.
      </p>
      {error && <p className="error" role="alert">{error}</p>}
      {notice && <p className="notice" role="status">{notice}</p>}
      {canStart && canReadCases && (
        <form className="form inline filters" onSubmit={(event) => void start(event)}>
          <label>
            Case
            <select value={caseId} onChange={(event) => setCaseId(event.target.value)}>
              <option value="">Choose a case</option>
              {cases.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.case_type} · {item.reconciliation_scope} · {shortId(item.id)} (
                  {item.status})
                </option>
              ))}
            </select>
          </label>
          <div className="actions">
            <button type="submit" disabled={busy || !caseId}>
              Start investigation
            </button>
          </div>
        </form>
      )}
      <section className="panel">
        <h2>Investigations</h2>
        {items.length === 0 ? (
          <p className="muted">No investigations yet.</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Case</th>
                  <th>Status</th>
                  <th>Proposed classification</th>
                  <th>Recommended playbook</th>
                  <th>Confidence</th>
                  <th>Started</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr key={item.investigation_id}>
                    <td>{caseLabel(item.case_id)}</td>
                    <td>
                      <StatusBadge status={item.status} />
                    </td>
                    <td>{item.proposed_classification ?? "—"}</td>
                    <td>{item.recommended_playbook ?? "—"}</td>
                    <td>{item.confidence ?? "—"}</td>
                    <td>{when(item.created_at)}</td>
                    <td>
                      <button
                        type="button"
                        className="secondary"
                        aria-label={`Open investigation ${item.investigation_id}`}
                        onClick={() => setSelected(item.investigation_id)}
                      >
                        Open
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
        <InvestigationDetail
          key={selected}
          api={api}
          id={selected}
          pollMs={pollMs}
          onSettled={() => setVersion((current) => current + 1)}
        />
      )}
    </section>
  );
}

type DetailProps = {
  api: InvestigationsApi;
  id: string;
  pollMs: number;
  onSettled: () => void;
};

// One investigation. While RUNNING it is re-read until the dispatcher's single terminal write.
function InvestigationDetail({ api, id, pollMs, onSettled }: DetailProps) {
  const [item, setItem] = useState<Investigation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [focus, setFocus] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let wasRunning = false;
    const load = () =>
      api
        .read(id)
        .then((found) => {
          if (!current) return;
          setItem(found);
          if (found.status === "RUNNING") {
            wasRunning = true;
            timer = setTimeout(load, pollMs);
          } else if (wasRunning) {
            onSettled();
          }
        })
        .catch((caught) => current && setError(messageOf(caught)));
    void load();
    return () => {
      current = false;
      clearTimeout(timer);
    };
    // onSettled only refreshes the list, so a new closure must not restart polling.
  }, [api, id, pollMs]);

  if (error) return <p className="error" role="alert">{error}</p>;
  if (!item) return null;
  const result = item.result;
  // Evidence IDs come from the server-written trace (AC-AI-04), so each one leads to the call
  // that returned it and to that call's stored output.
  const callFor = (ids: string[]) =>
    item.tool_calls.find((call) => call.evidence_ids.some((found) => ids.includes(found)));

  return (
    <section className="panel investigation-detail" aria-label="Investigation detail">
      <h2>
        Investigation {shortId(item.investigation_id)} <StatusBadge status={item.status} />
      </h2>
      <dl className="facts">
        <dt>Case</dt>
        <dd>{item.case_id}</dd>
        <dt>Model</dt>
        <dd>{item.model ?? "—"}</dd>
        <dt>Prompt version</dt>
        <dd>{item.prompt_version ?? "—"}</dd>
        <dt>Tool registry</dt>
        <dd>{item.tool_registry_version}</dd>
        <dt>Started</dt>
        <dd>{when(item.created_at)}</dd>
        <dt>Finished</dt>
        <dd>{when(item.finished_at)}</dd>
      </dl>
      {item.status === "RUNNING" && (
        <p className="notice" role="status">
          The agent is still working. This view refreshes until it finishes.
        </p>
      )}
      {item.status === "FAILED" && (
        <p className="error" role="alert">
          The investigation failed: {item.error ?? "no reason recorded"}.
        </p>
      )}
      {result && (
        <>
          <section>
            <h3>Likely cause</h3>
            <p>{result.root_cause_summary}</p>
            <dl className="facts">
              <dt>Classification</dt>
              <dd>
                {result.classification} <span className="badge">PROPOSED</span>
              </dd>
              <dt>Confidence</dt>
              <dd>{result.confidence}</dd>
              <dt>Recommended playbook</dt>
              <dd>{result.recommended_playbook ?? "None"}</dd>
              <dt>Approval</dt>
              <dd>{result.requires_approval ? "Required" : "Not required"}</dd>
            </dl>
            <p className="muted">
              The classification is a proposal; the case keeps its own type. Nothing here creates
              a request.
            </p>
          </section>
          <section>
            <h3>Evidence</h3>
            {result.evidence.length === 0 ? (
              <p className="muted">No evidence cited.</p>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Fact</th>
                      <th>Evidence</th>
                      <th>Record</th>
                      <th>Source</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {result.evidence.map((ref) => {
                      const call = callFor([ref.evidence_id, ref.record_id, ref.source_id]);
                      return (
                        <tr key={`${ref.evidence_id}-${ref.record_id}`}>
                          <td>{ref.fact}</td>
                          <td>{shortId(ref.evidence_id)}</td>
                          <td>{shortId(ref.record_id)}</td>
                          <td>{shortId(ref.source_id)}</td>
                          <td>
                            {call && (
                              <button
                                type="button"
                                className="secondary"
                                aria-label={`Open evidence ${ref.evidence_id}`}
                                onClick={() => setFocus(call.id)}
                              >
                                Open
                              </button>
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
          <section>
            <h3>Missing evidence</h3>
            {result.missing_evidence.length === 0 ? (
              <p className="muted">None named.</p>
            ) : (
              <ul>
                {result.missing_evidence.map((missing) => (
                  <li key={missing}>{missing}</li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}
      <ToolTrace calls={item.tool_calls} focus={focus} onFocus={setFocus} />
    </section>
  );
}

type TraceProps = { calls: ToolCall[]; focus: string | null; onFocus: (id: string | null) => void };

function ToolTrace({ calls, focus, onFocus }: TraceProps) {
  return (
    <section>
      <h3>Tool trace</h3>
      {calls.length === 0 ? (
        <p className="muted">No tool calls recorded.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>#</th>
                <th>Tool</th>
                <th>Permission</th>
                <th>Outcome</th>
                <th>Evidence</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {calls.map((call, index) => (
                <tr key={call.id} className={call.id === focus ? "highlight" : undefined}>
                  <td>{index + 1}</td>
                  <td>
                    {call.tool} <span className="muted">{call.tool_version}</span>
                  </td>
                  <td>{call.permission}</td>
                  <td>
                    <StatusBadge status={call.outcome} />
                    {call.error && <span className="muted"> {call.error}</span>}
                  </td>
                  <td>{call.evidence_ids.length}</td>
                  <td>
                    <button
                      type="button"
                      className="secondary"
                      aria-label={`Output of ${call.tool} call ${index + 1}`}
                      onClick={() => onFocus(call.id === focus ? null : call.id)}
                    >
                      {call.id === focus ? "Hide" : "Output"}
                    </button>
                    {call.id === focus && (
                      <pre className="trace-output" aria-label={`${call.tool} output`}>
                        {JSON.stringify(call.output, null, 2)}
                      </pre>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
