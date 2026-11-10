import { useState, type FormEvent } from "react";

import { ConfirmButton } from "./ConfirmButton";
import { GrantFields } from "./GrantFields";
import type { GrantInput, Invitation, PermissionSet } from "./types";

export function InviteForm({
  sets,
  busy,
  onInvite,
}: {
  sets: PermissionSet[];
  busy: boolean;
  onInvite: (email: string, grant: GrantInput) => Promise<boolean>;
}) {
  const [email, setEmail] = useState("");
  const [grant, setGrant] = useState<GrantInput | null>(null);
  // Remounts the grant fields after a successful invitation, clearing them.
  const [round, setRound] = useState(0);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!grant) return;
    if (await onInvite(email, grant)) {
      setEmail("");
      setGrant(null);
      setRound((current) => current + 1);
    }
  }

  return (
    <section className="panel">
      <h2>Invite a member</h2>
      <form className="form" onSubmit={submit}>
        <label>
          Email
          <input
            type="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
        </label>
        <GrantFields key={round} sets={sets} onChange={setGrant} />
        <button type="submit" disabled={busy || !grant}>
          Send invitation
        </button>
      </form>
    </section>
  );
}

function delivery(invitation: Invitation): string {
  if (invitation.delivery_status === "FAILED") {
    return "Not delivered. Resend to try again.";
  }
  return invitation.delivery_status === "SENT" ? "Sent" : invitation.delivery_status;
}

export function Invitations({
  invitations,
  busy,
  onResend,
  onRevoke,
}: {
  invitations: Invitation[];
  busy: boolean;
  onResend: (invitation: Invitation) => void;
  onRevoke: (invitation: Invitation) => void;
}) {
  return (
    <section className="panel">
      <h2>Invitations</h2>
      {invitations.length === 0 ? (
        <p className="muted">No invitations yet.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Email</th>
                <th>Status</th>
                <th>Email delivery</th>
                <th>Expires</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {invitations.map((invitation) => (
                <tr key={invitation.id}>
                  <td>{invitation.email}</td>
                  <td>{invitation.status}</td>
                  <td>{delivery(invitation)}</td>
                  <td>{new Date(invitation.expires_at).toLocaleString()}</td>
                  <td>
                    {invitation.status === "PENDING" && (
                      <span className="actions">
                        <button
                          type="button"
                          className="secondary"
                          disabled={busy}
                          onClick={() => onResend(invitation)}
                        >
                          Resend
                        </button>
                        <ConfirmButton
                          label="Revoke"
                          disabled={busy}
                          onConfirm={() => onRevoke(invitation)}
                        />
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
