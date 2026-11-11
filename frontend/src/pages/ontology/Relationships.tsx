import { useEffect, useState, type FormEvent } from "react";

import { ConfirmButton } from "../team/ConfirmButton";
import { relationshipNode, type RelationshipInput, type Term } from "./graph";

// Relationships in the current view. Only tenant relationships can change here; a core or pack
// term changes through a versioned pack release.
export function Relationships({
  terms,
  editable,
  busy,
  prefill,
  onAdd,
  onRemove,
}: {
  terms: Term[];
  editable: boolean;
  busy: boolean;
  prefill: { domain: string; range: string } | null;
  onAdd: (input: RelationshipInput) => Promise<boolean>;
  onRemove: (id: string) => void;
}) {
  const classes = terms.filter((term) => term.kind === "class");
  const relationships = terms.filter((term) => term.kind === "relationship");
  const labels = new Map(terms.map((term) => [term.id, term.label]));
  const name = (id: string | null) => (id ? (labels.get(id) ?? id) : "—");

  return (
    <section className="panel">
      <h2>Relationships</h2>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Relationship</th>
              <th>From</th>
              <th>To</th>
              <th>Layer</th>
              {editable && <th />}
            </tr>
          </thead>
          <tbody>
            {relationships.map((term) => (
              <tr key={term.id}>
                <td>
                  {term.label} <code className="muted">{term.id}</code>
                </td>
                <td>{name(term.domain)}</td>
                <td>{name(term.range)}</td>
                <td>{term.layer}</td>
                {editable && (
                  <td>
                    {term.layer === "tenant" && (
                      <ConfirmButton
                        label={`Remove ${term.label}`}
                        disabled={busy}
                        onConfirm={() => onRemove(term.id)}
                      />
                    )}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {relationships.length === 0 && <p className="muted">No relationships yet.</p>}
      {editable && (
        <RelationshipForm classes={classes} busy={busy} prefill={prefill} onAdd={onAdd} />
      )}
    </section>
  );
}

function RelationshipForm({
  classes,
  busy,
  prefill,
  onAdd,
}: {
  classes: Term[];
  busy: boolean;
  prefill: { domain: string; range: string } | null;
  onAdd: (input: RelationshipInput) => Promise<boolean>;
}) {
  const [label, setLabel] = useState("");
  const [domain, setDomain] = useState("");
  const [range, setRange] = useState("");

  useEffect(() => {
    if (!prefill) return;
    setDomain(prefill.domain);
    setRange(prefill.range);
  }, [prefill]);

  const valid = !!relationshipNode({ label, domain, range }) && !!domain && !!range;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (await onAdd({ label, domain, range })) setLabel("");
  }

  return (
    <form className="form grant-fields" onSubmit={submit} aria-label="Add a relationship">
      <label>
        Name
        <input value={label} onChange={(event) => setLabel(event.target.value)} />
      </label>
      <label>
        From
        <select value={domain} onChange={(event) => setDomain(event.target.value)}>
          <option value="">Choose a class</option>
          {classes.map((term) => (
            <option key={term.id} value={term.id}>
              {term.label}
            </option>
          ))}
        </select>
      </label>
      <label>
        To
        <select value={range} onChange={(event) => setRange(event.target.value)}>
          <option value="">Choose a class</option>
          {classes.map((term) => (
            <option key={term.id} value={term.id}>
              {term.label}
            </option>
          ))}
        </select>
      </label>
      <div className="actions">
        <button type="submit" disabled={busy || !valid}>
          Add relationship
        </button>
      </div>
      <p className="hint">
        The name becomes a tenant term, so it starts with a letter. Adding one with an existing
        name replaces it. You can also draw a line between two classes on the graph.
      </p>
    </form>
  );
}
