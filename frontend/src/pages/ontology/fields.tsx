import type { Term } from "./graph";
import type { SourceSummary, ValidationRun } from "./types";

// Pickers shared by the binding and authority editors. A source is chosen from the registry
// when the caller can read it, and typed as an ID otherwise.
export function SourceField({
  sources,
  value,
  onChange,
}: {
  sources: SourceSummary[];
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label>
      Source
      {sources.length > 0 ? (
        <select value={value} onChange={(event) => onChange(event.target.value)}>
          <option value="">Choose a source</option>
          {sources.map((source) => (
            <option key={source.id} value={source.id}>
              {source.display_name} ({source.connector_id})
            </option>
          ))}
        </select>
      ) : (
        <input value={value} placeholder="Source ID" onChange={(event) => onChange(event.target.value)} />
      )}
    </label>
  );
}

export function TermField({
  label,
  terms,
  value,
  onChange,
}: {
  label: string;
  terms: Term[];
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label>
      {label}
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="">Choose a term</option>
        {terms.map((term) => (
          <option key={term.id} value={term.id}>
            {term.label} ({term.id})
          </option>
        ))}
      </select>
    </label>
  );
}

const FAILED_MAPPINGS = (run: ValidationRun | null) =>
  (run?.results ?? []).filter((result) => result.validator.startsWith("mappings.") && !result.passed);

function names(message: string, term: string): boolean {
  const escaped = term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(`(^|[^A-Za-z0-9_:])${escaped}($|[^A-Za-z0-9_])`).test(message);
}

// The mappings.* messages of the last validation run, split by the row whose terms they name.
// Messages are plain text, so a row claims a message that names one of its terms.
export function messagesByRow<R extends { id: string }>(
  run: ValidationRun | null,
  rows: R[],
  termsOf: (row: R) => string[],
): { byRow: Map<string, string[]>; unmatched: string[] } {
  const byRow = new Map<string, string[]>();
  const unmatched: string[] = [];
  for (const result of FAILED_MAPPINGS(run)) {
    for (const message of result.messages) {
      const owners = rows.filter((row) => termsOf(row).some((term) => names(message, term)));
      if (owners.length === 0) unmatched.push(message);
      for (const owner of owners) byRow.set(owner.id, [...(byRow.get(owner.id) ?? []), message]);
    }
  }
  return { byRow, unmatched };
}

export function RowMessages({ messages }: { messages: string[] | undefined }) {
  if (!messages?.length) return null;
  return (
    <ul className="row-messages">
      {messages.map((message) => (
        <li key={message} className="error">
          {message}
        </li>
      ))}
    </ul>
  );
}
