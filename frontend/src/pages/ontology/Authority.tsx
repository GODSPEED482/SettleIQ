import { useState, type FormEvent } from "react";

import { ConfirmButton } from "../team/ConfirmButton";
import { messagesByRow, RowMessages, SourceField, TermField } from "./fields";
import type { Term } from "./graph";
import type { Authority as AuthorityRow, AuthorityInput, SourceSummary, ValidationRun } from "./types";

// Authority assertions on the open draft (spec 5.4): which source is authoritative for a
// property of an entity type, over an interval, and how that was verified.

export type AuthorityDraft = Omit<AuthorityInput, "draft_id">;

const EMPTY: AuthorityDraft = {
  property_iri: "",
  entity_type_iri: "",
  source_id: "",
  valid_from: "",
  valid_to: null,
  verification_method: "",
};

// datetime-local has no zone; the API needs one, so the browser's zone is applied.
const withZone = (local: string) => (local ? new Date(local).toISOString() : local);

export function Authority({
  rows,
  sources,
  classes,
  properties,
  run,
  editable,
  busy,
  onSave,
  onRetire,
}: {
  rows: AuthorityRow[];
  sources: SourceSummary[];
  classes: Term[];
  properties: Term[];
  run: ValidationRun | null;
  editable: boolean;
  busy: boolean;
  onSave: (assertion: AuthorityDraft) => Promise<boolean>;
  onRetire: (row: AuthorityRow) => void;
}) {
  const sourceName = new Map(sources.map((source) => [source.id, source.display_name]));
  const { byRow } = messagesByRow(run, rows, (row) => [row.property_iri, row.entity_type_iri]);
  return (
    <section className="panel" aria-label="Authority">
      <h2>Authority</h2>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Property</th>
              <th>Authoritative source</th>
              <th>Valid</th>
              <th>Verified by</th>
              {editable && <th />}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <td>
                  <code>{row.property_iri}</code> of <code>{row.entity_type_iri}</code>
                  <RowMessages messages={byRow.get(row.id)} />
                </td>
                <td>{sourceName.get(row.source_id) ?? row.source_id}</td>
                <td>
                  from {row.valid_from}
                  {row.valid_to ? ` to ${row.valid_to}` : ", open-ended"}
                </td>
                <td>
                  {row.verification_method}
                  <div className="muted">version {row.version}</div>
                </td>
                {editable && (
                  <td>
                    <ConfirmButton label="Retire" disabled={busy} onConfirm={() => onRetire(row)} />
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {rows.length === 0 && <p className="muted">No authority assertions on this draft yet.</p>}
      {editable && (
        <AuthorityForm sources={sources} classes={classes} properties={properties} busy={busy} onSave={onSave} />
      )}
    </section>
  );
}

function AuthorityForm({
  sources,
  classes,
  properties,
  busy,
  onSave,
}: {
  sources: SourceSummary[];
  classes: Term[];
  properties: Term[];
  busy: boolean;
  onSave: (assertion: AuthorityDraft) => Promise<boolean>;
}) {
  const [assertion, setAssertion] = useState<AuthorityDraft>(EMPTY);
  const set = <K extends keyof AuthorityDraft>(key: K, value: AuthorityDraft[K]) =>
    setAssertion((current) => ({ ...current, [key]: value }));

  async function submit(event: FormEvent) {
    event.preventDefault();
    const saved = await onSave({
      ...assertion,
      valid_from: withZone(assertion.valid_from),
      valid_to: assertion.valid_to ? withZone(assertion.valid_to) : null,
    });
    if (saved) setAssertion(EMPTY);
  }

  return (
    <form className="form" onSubmit={submit} aria-label="Add an authority assertion">
      <h3>Add an authority assertion</h3>
      <div className="grant-fields">
        <TermField
          label="Property"
          terms={properties}
          value={assertion.property_iri}
          onChange={(value) => set("property_iri", value)}
        />
        <TermField
          label="Entity type"
          terms={classes}
          value={assertion.entity_type_iri}
          onChange={(value) => set("entity_type_iri", value)}
        />
        <SourceField sources={sources} value={assertion.source_id} onChange={(value) => set("source_id", value)} />
        <label>
          Valid from
          <input
            type="datetime-local"
            value={assertion.valid_from}
            onChange={(event) => set("valid_from", event.target.value)}
          />
        </label>
        <label>
          Valid to
          <input
            type="datetime-local"
            value={assertion.valid_to ?? ""}
            onChange={(event) => set("valid_to", event.target.value || null)}
          />
        </label>
        <label>
          Verified by
          <input
            value={assertion.verification_method}
            onChange={(event) => set("verification_method", event.target.value)}
          />
        </label>
      </div>
      <div className="actions">
        <button type="submit" disabled={busy}>
          Save assertion
        </button>
      </div>
    </form>
  );
}
