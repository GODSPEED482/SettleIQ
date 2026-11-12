import { useEffect, useState, type FormEvent } from "react";

import { ConfirmButton } from "../team/ConfirmButton";
import { messagesByRow, RowMessages, SourceField, TermField } from "./fields";
import type { Term } from "./graph";
import type {
  BindingProposal,
  Mapping,
  MappingInput,
  SourceSummary,
  TransformName,
  ValidationRun,
} from "./types";

// Source bindings on the open draft (spec 5.3). Each binding is versioned and append-only:
// saving the same source, object, field and property again creates its next version.

export type BindingDraft = Omit<MappingInput, "draft_id">;

const EMPTY: BindingDraft = {
  source_id: "",
  external_object: "",
  field_path: "",
  property_iri: "",
  class_iri: "",
  datatype: "string",
  transform: { name: "identity" },
  nullable: false,
  is_unique: false,
  sync_strategy: "batch",
  value_basis: "",
  unit: null,
  currency_field: null,
  temporal_context: "",
  method: "human",
};

export function bindingFromProposal(proposal: BindingProposal): BindingDraft {
  return {
    ...EMPTY,
    ...proposal.mapping,
    source_id: proposal.source_id,
    external_object: proposal.object,
    field_path: proposal.field,
    property_iri: proposal.property,
    method: "accepted_proposal",
  };
}

export const bindingKey = (row: {
  source_id: string;
  external_object: string;
  field_path: string;
  property_iri: string;
}) => [row.source_id, row.external_object, row.field_path, row.property_iri].join("|");

export function Bindings({
  mappings,
  sources,
  classes,
  properties,
  run,
  editable,
  busy,
  prefill,
  onSave,
  onRetire,
}: {
  mappings: Mapping[];
  sources: SourceSummary[];
  classes: Term[];
  properties: Term[];
  run: ValidationRun | null;
  editable: boolean;
  busy: boolean;
  prefill: BindingDraft | null;
  onSave: (binding: BindingDraft) => Promise<boolean>;
  onRetire: (mapping: Mapping) => void;
}) {
  const sourceName = new Map(sources.map((source) => [source.id, source.display_name]));
  const { byRow, unmatched } = messagesByRow(run, mappings, (row) => [row.property_iri, row.class_iri]);
  return (
    <section className="panel" aria-label="Bindings">
      <h2>Bindings</h2>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Source field</th>
              <th>Property</th>
              <th>Type</th>
              <th>Meaning</th>
              {editable && <th />}
            </tr>
          </thead>
          <tbody>
            {mappings.map((mapping) => (
              <tr key={mapping.id}>
                <td>
                  {sourceName.get(mapping.source_id) ?? mapping.source_id}: {mapping.external_object}.
                  {mapping.field_path}
                  {mapping.method === "accepted_proposal" && <span className="badge">from proposal</span>}
                  <RowMessages messages={byRow.get(mapping.id)} />
                </td>
                <td>
                  <code>{mapping.property_iri}</code> on <code>{mapping.class_iri}</code>
                </td>
                <td>
                  {mapping.datatype}, {mapping.transform.name}
                  {mapping.nullable ? ", nullable" : ""}
                  {mapping.is_unique ? ", unique" : ""}
                </td>
                <td>
                  {mapping.value_basis}, {mapping.temporal_context}
                  {mapping.unit ? `, ${mapping.unit}` : ""}
                  {mapping.currency_field ? `, currency in ${mapping.currency_field}` : ""}
                  <div className="muted">version {mapping.version}</div>
                </td>
                {editable && (
                  <td>
                    <ConfirmButton label="Retire" disabled={busy} onConfirm={() => onRetire(mapping)} />
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {mappings.length === 0 && <p className="muted">No bindings on this draft yet.</p>}
      {unmatched.length > 0 && (
        <>
          <p className="muted">Other binding checks from the last validation:</p>
          <RowMessages messages={unmatched} />
        </>
      )}
      {editable && (
        <BindingForm
          sources={sources}
          classes={classes}
          properties={properties}
          busy={busy}
          prefill={prefill}
          onSave={onSave}
        />
      )}
    </section>
  );
}

function parseEnumValues(text: string): Record<string, string> {
  const values: Record<string, string> = {};
  for (const line of text.split("\n")) {
    const [from, ...to] = line.split("=");
    if (from.trim()) values[from.trim()] = to.join("=").trim();
  }
  return values;
}

function BindingForm({
  sources,
  classes,
  properties,
  busy,
  prefill,
  onSave,
}: {
  sources: SourceSummary[];
  classes: Term[];
  properties: Term[];
  busy: boolean;
  prefill: BindingDraft | null;
  onSave: (binding: BindingDraft) => Promise<boolean>;
}) {
  const [binding, setBinding] = useState<BindingDraft>(EMPTY);
  const [enumText, setEnumText] = useState("");

  useEffect(() => {
    if (prefill) setBinding(prefill);
  }, [prefill]);

  const set = <K extends keyof BindingDraft>(key: K, value: BindingDraft[K]) =>
    setBinding((current) => ({ ...current, [key]: value }));

  async function submit(event: FormEvent) {
    event.preventDefault();
    const transform =
      binding.transform.name === "enum_map"
        ? { name: "enum_map" as const, values: parseEnumValues(enumText) }
        : { name: binding.transform.name };
    if (await onSave({ ...binding, transform })) {
      setBinding(EMPTY);
      setEnumText("");
    }
  }

  return (
    <form className="form" onSubmit={submit} aria-label="Add a binding">
      <h3>{binding.method === "accepted_proposal" ? "Bind the accepted proposal" : "Add a binding"}</h3>
      <div className="grant-fields">
        <SourceField sources={sources} value={binding.source_id} onChange={(value) => set("source_id", value)} />
        <label>
          Object
          <input value={binding.external_object} onChange={(event) => set("external_object", event.target.value)} />
        </label>
        <label>
          Field
          <input value={binding.field_path} onChange={(event) => set("field_path", event.target.value)} />
        </label>
        <TermField label="Class" terms={classes} value={binding.class_iri} onChange={(value) => set("class_iri", value)} />
        <TermField
          label="Property"
          terms={properties}
          value={binding.property_iri}
          onChange={(value) => set("property_iri", value)}
        />
        <label>
          Data type
          <select
            value={binding.datatype}
            onChange={(event) => set("datatype", event.target.value as BindingDraft["datatype"])}
          >
            {["string", "decimal", "integer", "boolean", "datetime"].map((name) => (
              <option key={name}>{name}</option>
            ))}
          </select>
        </label>
        <label>
          Transform
          <select
            value={binding.transform.name}
            onChange={(event) => set("transform", { name: event.target.value as TransformName })}
          >
            {["identity", "decimal_string", "upper", "lower", "iso8601", "enum_map"].map((name) => (
              <option key={name}>{name}</option>
            ))}
          </select>
        </label>
        <label>
          Sync
          <select
            value={binding.sync_strategy}
            onChange={(event) => set("sync_strategy", event.target.value as BindingDraft["sync_strategy"])}
          >
            {["batch", "event", "file"].map((name) => (
              <option key={name}>{name}</option>
            ))}
          </select>
        </label>
        <label>
          Value basis
          <input value={binding.value_basis} onChange={(event) => set("value_basis", event.target.value)} />
        </label>
        <label>
          Time context
          <input value={binding.temporal_context} onChange={(event) => set("temporal_context", event.target.value)} />
        </label>
        <label>
          Unit
          <input value={binding.unit ?? ""} onChange={(event) => set("unit", event.target.value || null)} />
        </label>
        <label>
          Currency field
          <input
            value={binding.currency_field ?? ""}
            onChange={(event) => set("currency_field", event.target.value || null)}
          />
        </label>
      </div>
      {binding.transform.name === "enum_map" && (
        <label>
          Value map, one <code>source=target</code> per line
          <textarea rows={3} value={enumText} onChange={(event) => setEnumText(event.target.value)} />
        </label>
      )}
      <div className="actions">
        <label className="check">
          <input type="checkbox" checked={binding.nullable} onChange={(event) => set("nullable", event.target.checked)} />
          Nullable
        </label>
        <label className="check">
          <input type="checkbox" checked={binding.is_unique} onChange={(event) => set("is_unique", event.target.checked)} />
          Unique
        </label>
      </div>
      <div className="actions">
        <button type="submit" disabled={busy}>
          Save binding
        </button>
        {binding.method === "accepted_proposal" && (
          <button type="button" className="secondary" onClick={() => setBinding(EMPTY)}>
            Clear
          </button>
        )}
      </div>
    </form>
  );
}
