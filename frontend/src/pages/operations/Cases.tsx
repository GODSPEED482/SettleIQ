import { useEffect, useState, type FormEvent } from "react";

import { messageOf } from "../../api/client";
import type { OperationsApi } from "./api";
import { ProvenanceLabel, StatusBadge, amount } from "./labels";
import {
  NEEDS_REASON,
  TRIAGE_TRANSITIONS,
  type CaseStatus,
  type DomainCase,
  type TeamMember,
  type TriageInput,
} from "./types";

// TODO(G5 5.11 Suprava): on case review, "Create playbook request" (playbook.request) for the
// recommended playbook. Send only the case and the playbook's declared parameters, never
// approval, decision or execution fields (spec 12.1); the backend decides the rest.
const STATUSES: CaseStatus[] = [
  "OPEN",
  "INVESTIGATING",
  "HUMAN_REVIEW",
  "ACTION_PENDING",
  "RESOLVED",
  "DISMISSED",
  "CLOSED",
];

type Props = {
  api: OperationsApi;
  version: number;
  canTriage: boolean;
  members: TeamMember[];
  busy: boolean;
  onTriage: (item: DomainCase, body: TriageInput) => Promise<boolean>;
  onReopen: (item: DomainCase, reason: string) => Promise<boolean>;
};

// Cases by scope, severity and status (spec 9.3). Triage is reasoned and follows the case state
// machine; execution states are never set from here.
export function Cases({ api, version, canTriage, members, busy, onTriage, onReopen }: Props) {
  const [cases, setCases] = useState<DomainCase[]>([]);
  const [status, setStatus] = useState("");
  const [caseType, setCaseType] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    api
      .cases({ status, case_type: caseType })
      .then((list) => current && setCases(list))
      .catch((caught) => current && setError(messageOf(caught)));
    return () => {
      current = false;
    };
  }, [api, version, status, caseType]);

  const types = [...new Set(cases.map((item) => item.case_type))].sort();
  const active = cases.find((item) => item.id === selected) ?? null;

  return (
    <section className="panel">
      <h2>Cases</h2>
      {error && <p className="error" role="alert">{error}</p>}
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
        <label>
          Case type
          <select value={caseType} onChange={(event) => setCaseType(event.target.value)}>
            <option value="">Any</option>
            {[...new Set([...types, caseType].filter(Boolean))].map((value) => (
              <option key={value}>{value}</option>
            ))}
          </select>
        </label>
      </div>
      {cases.length === 0 ? (
        <p className="muted">No cases match.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Case type</th>
                <th>Scope</th>
                <th>Severity</th>
                <th>Status</th>
                <th>Expected</th>
                <th>Observed</th>
                <th>Evidence</th>
                {canTriage && <th />}
              </tr>
            </thead>
            <tbody>
              {cases.map((item) => (
                <tr key={item.id}>
                  <td>{item.case_type}</td>
                  <td>{item.reconciliation_scope}</td>
                  <td>{item.severity}</td>
                  <td>
                    <StatusBadge status={item.status} />
                  </td>
                  <td>{amount(item.expected_amount, item.currency)}</td>
                  <td>{amount(item.observed_amount, item.currency)}</td>
                  <td>
                    {item.evidence_provenance.map((provenance) => (
                      <ProvenanceLabel key={provenance} provenance={provenance} />
                    ))}
                  </td>
                  {canTriage && (
                    <td>
                      <button
                        type="button"
                        className="secondary"
                        aria-label={`Triage ${item.case_type} ${item.id}`}
                        onClick={() => setSelected(item.id === selected ? null : item.id)}
                      >
                        Triage
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {canTriage && active && (
        <TriageForm
          key={active.id}
          item={active}
          members={members}
          busy={busy}
          onTriage={onTriage}
          onReopen={onReopen}
        />
      )}
    </section>
  );
}

type FormProps = Pick<Props, "members" | "busy" | "onTriage" | "onReopen"> & { item: DomainCase };

function TriageForm({ item, members, busy, onTriage, onReopen }: FormProps) {
  const moves = TRIAGE_TRANSITIONS[item.status] ?? [];
  const [next, setNext] = useState("");
  const [assignee, setAssignee] = useState("");
  const [comment, setComment] = useState("");
  const [reason, setReason] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const reopening = item.status === "CLOSED";
  const needsReason = reopening || NEEDS_REASON.includes(next as CaseStatus);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setProblem(null);
    if (needsReason && !reason.trim()) {
      setProblem("Give a reason.");
      return;
    }
    if (reopening) {
      await onReopen(item, reason.trim());
      return;
    }
    const body: TriageInput = {};
    if (next) body.status = next as CaseStatus;
    if (assignee) body.assignee_id = assignee;
    if (comment.trim()) body.comment = comment.trim();
    if (reason.trim()) body.reason = reason.trim();
    if (!body.status && !body.assignee_id && !body.comment) {
      setProblem("Choose a status, an assignee or a comment.");
      return;
    }
    if (await onTriage(item, body)) {
      setNext("");
      setComment("");
      setReason("");
    }
  }

  return (
    <form className="form triage" onSubmit={(event) => void submit(event)}>
      <h3>
        {item.case_type} <StatusBadge status={item.status} />
      </h3>
      {item.status === "ACTION_PENDING" && (
        <p className="muted">A side effect is in flight, so this case cannot be changed.</p>
      )}
      {!reopening && moves.length > 0 && (
        <label>
          Move to
          <select value={next} onChange={(event) => setNext(event.target.value)}>
            <option value="">No change</option>
            {moves.map((value) => (
              <option key={value}>{value}</option>
            ))}
          </select>
        </label>
      )}
      {!reopening && members.length > 0 && (
        <label>
          Assignee
          <select value={assignee} onChange={(event) => setAssignee(event.target.value)}>
            <option value="">No change</option>
            {members
              .filter((member) => member.status === "ACTIVE")
              .map((member) => (
                <option key={member.user_id} value={member.user_id}>
                  {member.email}
                </option>
              ))}
          </select>
        </label>
      )}
      {!reopening && (
        <label>
          Comment
          <input value={comment} onChange={(event) => setComment(event.target.value)} />
        </label>
      )}
      <label>
        Reason{needsReason ? " (required)" : ""}
        <input value={reason} onChange={(event) => setReason(event.target.value)} />
      </label>
      {problem && <p className="error" role="alert">{problem}</p>}
      <div className="actions">
        <button type="submit" disabled={busy || item.status === "ACTION_PENDING"}>
          {reopening ? "Reopen" : "Save triage"}
        </button>
      </div>
    </form>
  );
}
