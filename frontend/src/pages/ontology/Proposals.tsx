import type { ReactNode } from "react";

import type { BindingProposal, ProposalEvidence, TermProposal } from "./types";

// Generated proposals stay apart from accepted terms (spec 5.7, AC-ONT-03). Each piece of
// evidence says whether the server observed it in discovered metadata or the model inferred it.
// A proposal changes the draft only when a person accepts it.
export function Proposals({
  terms,
  bindings,
  accepted,
  bindingAction,
  bindingToolbar,
  canAccept,
  busy,
  onAccept,
}: {
  terms: TermProposal[];
  bindings: BindingProposal[];
  accepted: (id: string) => boolean;
  bindingAction?: (proposal: BindingProposal) => ReactNode;
  bindingToolbar?: ReactNode;
  canAccept: boolean;
  busy: boolean;
  onAccept: (proposal: TermProposal) => void;
}) {
  if (terms.length === 0 && bindings.length === 0) return null;
  return (
    <section className="panel" aria-label="Generated proposals">
      <h2>Generated proposals</h2>
      <p className="muted">
        Drafted from discovered metadata. None of these is part of the draft until you accept it.
      </p>
      {terms.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Proposed term</th>
                <th>Links</th>
                <th>Evidence</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {terms.map((proposal) => (
                <tr key={proposal.id}>
                  <td>
                    {proposal.label} <span className="badge">{proposal.kind}</span>
                    <div>
                      <code className="muted">{proposal.id}</code>
                    </div>
                  </td>
                  <td>{links(proposal)}</td>
                  <td>
                    <Evidence items={proposal.evidence} />
                  </td>
                  <td>
                    {accepted(proposal.id) ? (
                      <span className="badge">Accepted</span>
                    ) : (
                      canAccept && (
                        <button
                          type="button"
                          className="secondary"
                          disabled={busy}
                          onClick={() => onAccept(proposal)}
                        >
                          Accept {proposal.label}
                        </button>
                      )
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {bindings.length > 0 && bindingToolbar}
      {bindings.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Proposed binding</th>
                <th>Property</th>
                <th>Evidence</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {bindings.map((proposal) => (
                <tr key={`${proposal.source_id}:${proposal.object}:${proposal.field}:${proposal.property}`}>
                  <td>
                    {proposal.object}.{proposal.field}{" "}
                    <span className="badge">{proposal.confidence} confidence</span>
                  </td>
                  <td>
                    <code>{proposal.property}</code>
                    {proposal.class_iri && (
                      <>
                        {" "}
                        on <code>{proposal.class_iri}</code>
                      </>
                    )}
                  </td>
                  <td>
                    <Evidence items={proposal.evidence} />
                  </td>
                  <td>{bindingAction?.(proposal)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function links(proposal: TermProposal): string {
  if (proposal.kind === "class") return proposal.subclass_of ? `subclass of ${proposal.subclass_of}` : "—";
  if (proposal.kind === "relationship") return `${proposal.domain ?? "?"} → ${proposal.range ?? "?"}`;
  return proposal.domain ? `on ${proposal.domain}` : "—";
}

function Evidence({ items }: { items: ProposalEvidence[] }) {
  return (
    <ul className="evidence">
      {items.map((item, index) => (
        <li key={index}>
          <span className={`basis basis-${item.basis}`}>{item.basis}</span> {item.detail}
          {item.rationale && <div className="hint">Model's reading: {item.rationale}</div>}
        </li>
      ))}
    </ul>
  );
}
