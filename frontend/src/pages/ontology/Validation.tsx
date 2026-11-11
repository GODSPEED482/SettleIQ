import type { Draft, ValidationRun } from "./types";

// Validation of the exact candidate: the draft's tenant terms, bindings and authority together.
// A run belongs to one candidate hash, so any later change leaves it out of date.
export function Validation({
  draft,
  run,
  canValidate,
  busy,
  onValidate,
}: {
  draft: Draft;
  run: ValidationRun | null;
  canValidate: boolean;
  busy: boolean;
  onValidate: () => void;
}) {
  const current = !!run && run.content_hash === draft.content_hash && run.revision === draft.revision;
  return (
    <section className="panel" aria-label="Validation">
      <h2>Validation</h2>
      {!run && <p className="muted">This draft has not been validated in this session.</p>}
      {run && (
        <p role="status">
          {run.passed ? "Validation passed" : "Validation failed"} for revision {run.revision}.
          {!current && " The draft has changed since, so this result is out of date."}
        </p>
      )}
      {canValidate && (
        <div className="actions">
          <button type="button" className="secondary" disabled={busy} onClick={onValidate}>
            Validate draft
          </button>
        </div>
      )}
    </section>
  );
}
