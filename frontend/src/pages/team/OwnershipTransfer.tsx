import { useState } from "react";

import type { Member } from "./types";

// The only route that changes the owner pointer (spec 3.2). The former owner keeps only what
// was granted to them separately.
export function OwnershipTransfer({
  members,
  busy,
  onTransfer,
}: {
  members: Member[];
  busy: boolean;
  onTransfer: (member: Member) => void;
}) {
  const candidates = members.filter((member) => member.status === "ACTIVE" && !member.owns_tenant);
  const [target, setTarget] = useState("");
  const [confirming, setConfirming] = useState(false);
  const chosen = candidates.find((member) => member.user_id === target);

  return (
    <section className="panel">
      <h2>Transfer ownership</h2>
      {candidates.length === 0 ? (
        <p className="muted">Ownership can go only to another active member.</p>
      ) : (
        <div className="form narrow">
          <label>
            New owner
            <select
              value={target}
              onChange={(event) => {
                setTarget(event.target.value);
                setConfirming(false);
              }}
            >
              <option value="" disabled>
                Choose a member
              </option>
              {candidates.map((member) => (
                <option key={member.user_id} value={member.user_id}>
                  {member.email}
                </option>
              ))}
            </select>
          </label>
          {!confirming ? (
            <button type="button" disabled={!chosen || busy} onClick={() => setConfirming(true)}>
              Transfer ownership
            </button>
          ) : (
            chosen && (
              <div className="notice">
                <p>
                  Make <strong>{chosen.email}</strong> the owner? You lose owner access at once and
                  keep only what you were granted separately.
                </p>
                <span className="actions">
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => {
                      setConfirming(false);
                      onTransfer(chosen);
                    }}
                  >
                    Confirm transfer
                  </button>
                  <button type="button" className="secondary" onClick={() => setConfirming(false)}>
                    Cancel
                  </button>
                </span>
              </div>
            )
          )}
        </div>
      )}
    </section>
  );
}
