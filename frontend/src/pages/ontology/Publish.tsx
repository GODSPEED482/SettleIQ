import { useState } from "react";

import type { Draft, ValidationRun } from "./types";
import { isCurrentRun } from "./Validation";

// Publication of the exact candidate a passed run checked (spec 5.6, AC-ONT-05, AC-ONT-06). The
// button opens only for a passed run whose hash and revision match the draft as it is now, and
// the person confirms against that hash and run before anything is sent.
export function Publish({
  draft,
  run,
  nextVersion,
  busy,
  onPublish,
}: {
  draft: Draft;
  run: ValidationRun | null;
  nextVersion: number;
  busy: boolean;
  onPublish: (run: ValidationRun) => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const ready = isCurrentRun(run, draft) && run.passed;
  const reason = !run
    ? "Validate the draft before publishing it."
    : !isCurrentRun(run, draft)
      ? "The draft changed after it was validated. Validate it again."
      : !run.passed
        ? "The last validation failed. Fix the failures and validate again."
        : null;

  return (
    <section className="panel" aria-label="Publish">
      <h2>Publish</h2>
      {reason && <p className="muted">{reason}</p>}
      {!confirming && (
        <div className="actions">
          <button type="button" disabled={busy || !ready} onClick={() => setConfirming(true)}>
            Publish version {nextVersion}
          </button>
        </div>
      )}
      {confirming && ready && (
        <div className="notice" role="group" aria-label="Confirm publication">
          <p>
            Publish version {nextVersion} from revision {draft.revision}. Published versions are
            immutable, and runs already started keep the version they pinned.
          </p>
          <p>
            Candidate hash <code className="hash">{run.content_hash}</code>
            <br />
            Validation run <code>{run.validation_run_id}</code>, passed {run.results.length} of{" "}
            {run.results.length} validators.
          </p>
          <div className="actions">
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                setConfirming(false);
                onPublish(run);
              }}
            >
              Confirm publish
            </button>
            <button type="button" className="secondary" onClick={() => setConfirming(false)}>
              Cancel
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
