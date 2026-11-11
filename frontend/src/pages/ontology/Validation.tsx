import type { Draft, ValidationRun } from "./types";

// Validation of the exact candidate: the draft's tenant terms, bindings and authority together.
// A run belongs to one candidate hash, so any later change leaves it out of date.
export function isCurrentRun(run: ValidationRun | null, draft: Draft): run is ValidationRun {
  return !!run && run.content_hash === draft.content_hash && run.revision === draft.revision;
}

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
  const current = isCurrentRun(run, draft);
  return (
    <section className="panel" aria-label="Validation">
      <h2>Validation</h2>
      <p>
        Candidate hash <code className="hash">{draft.content_hash}</code>
      </p>
      {!run && <p className="muted">This draft has not been validated in this session.</p>}
      {run && (
        <>
          <p role="status">
            {run.passed ? "Validation passed" : "Validation failed"} for revision {run.revision}.
            {!current && " The draft has changed since, so this result is out of date."}
          </p>
          <p className="muted">
            Run <code>{run.validation_run_id}</code> checked hash{" "}
            <code className="hash">{run.content_hash}</code>
          </p>
          <div className="table-wrap">
            <table aria-label="Validator results">
              <thead>
                <tr>
                  <th>Validator</th>
                  <th>Kind</th>
                  <th>Result</th>
                  <th>Messages</th>
                </tr>
              </thead>
              <tbody>
                {run.results.map((result) => (
                  <tr key={result.validator}>
                    <td>
                      <code>{result.validator}</code>
                    </td>
                    <td>{result.kind}</td>
                    <td className={result.passed ? undefined : "error"}>
                      {result.passed ? "Passed" : "Failed"}
                    </td>
                    <td>
                      {result.messages.length > 0 && (
                        <ul className="row-messages">
                          {result.messages.map((message) => (
                            <li key={message}>{message}</li>
                          ))}
                        </ul>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
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
