import { useEffect, useState } from "react";

import { messageOf } from "../../api/client";
import type { OperationsApi } from "./api";
import { shortId } from "./labels";
import type { DecisionInput, ResolutionDecision } from "./types";

type Props = {
  api: OperationsApi;
  version: number;
  canReview: boolean;
  busy: boolean;
  onDecide: (decision: ResolutionDecision, body: DecisionInput) => Promise<boolean>;
};

// Ambiguous identity links wait here; nothing picks a candidate for the reviewer (AC-REC-06).
export function IdentityReview({ api, version, canReview, busy, onDecide }: Props) {
  const [decisions, setDecisions] = useState<ResolutionDecision[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    api
      .pendingDecisions()
      .then((list) => current && setDecisions(list))
      .catch((caught) => current && setError(messageOf(caught)));
    return () => {
      current = false;
    };
  }, [api, version]);

  return (
    <section className="panel">
      <h2>Identity review</h2>
      {error && <p className="error" role="alert">{error}</p>}
      {decisions.length === 0 ? (
        <p className="muted">No identity links wait for review.</p>
      ) : (
        decisions.map((decision) => (
          <Decision
            key={decision.id}
            decision={decision}
            canReview={canReview}
            busy={busy}
            onDecide={onDecide}
          />
        ))
      )}
    </section>
  );
}

type DecisionProps = Omit<Props, "api" | "version"> & { decision: ResolutionDecision };

function Decision({ decision, canReview, busy, onDecide }: DecisionProps) {
  const [reason, setReason] = useState("");
  const [problem, setProblem] = useState<string | null>(null);

  function decide(body: (reason: string) => DecisionInput) {
    if (!reason.trim()) {
      setProblem("Give a reason.");
      return;
    }
    setProblem(null);
    void onDecide(decision, body(reason.trim()));
  }

  return (
    <article className="decision" aria-label={`Decision ${shortId(decision.id)}`}>
      <p>
        {decision.class_iri} from record{" "}
        <code title={decision.source_record_id}>{shortId(decision.source_record_id)}</code>
      </p>
      <p className="muted">
        {Object.entries(decision.features)
          .map(([key, value]) => `${key}: ${JSON.stringify(value)}`)
          .join(" · ")}
      </p>
      <ul className="evidence">
        {decision.candidate_entity_ids.map((candidate) => (
          <li key={candidate}>
            Candidate <code title={candidate}>{shortId(candidate)}</code>
            {canReview && (
              <button
                type="button"
                className="secondary"
                disabled={busy}
                aria-label={`Accept ${candidate}`}
                onClick={() =>
                  decide((text) => ({
                    outcome: "ACCEPT",
                    candidate_entity_id: candidate,
                    reason: text,
                  }))
                }
              >
                Accept
              </button>
            )}
          </li>
        ))}
      </ul>
      {canReview && (
        <div className="form inline">
          <label>
            Reason (required)
            <input value={reason} onChange={(event) => setReason(event.target.value)} />
          </label>
          <div className="actions">
            <button
              type="button"
              className="secondary"
              disabled={busy}
              onClick={() => decide((text) => ({ outcome: "REJECT", reason: text }))}
            >
              Reject, leave unlinked
            </button>
          </div>
          {problem && <p className="error" role="alert">{problem}</p>}
        </div>
      )}
    </article>
  );
}
