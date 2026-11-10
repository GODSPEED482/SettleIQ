import { useState } from "react";

import { ConfirmButton } from "./ConfirmButton";
import { GrantFields } from "./GrantFields";
import type { Grant, GrantInput, Member, PermissionSet } from "./types";

export type MemberActions = {
  addGrant: (member: Member, grant: GrantInput) => void;
  revokeGrant: (member: Member, grant: Grant) => void;
  suspend: (member: Member) => void;
  reinstate: (member: Member) => void;
  remove: (member: Member) => void;
};

function scopeLabel(grant: Grant): string {
  return grant.scope_type ? ` (${grant.scope_type}: ${grant.scope_id})` : "";
}

export function Members({
  members,
  sets,
  canAssign,
  canRemove,
  busy,
  actions,
}: {
  members: Member[];
  sets: PermissionSet[];
  canAssign: boolean;
  canRemove: boolean;
  busy: boolean;
  actions: MemberActions;
}) {
  return (
    <section className="panel">
      <h2>Members</h2>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Email</th>
              <th>Status</th>
              <th>Grants</th>
              {(canAssign || canRemove) && <th>Actions</th>}
            </tr>
          </thead>
          <tbody>
            {members.map((member) => (
              <MemberRow
                key={member.user_id}
                member={member}
                sets={sets}
                canAssign={canAssign}
                canRemove={canRemove}
                busy={busy}
                actions={actions}
              />
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function MemberRow({
  member,
  sets,
  canAssign,
  canRemove,
  busy,
  actions,
}: {
  member: Member;
  sets: PermissionSet[];
  canAssign: boolean;
  canRemove: boolean;
  busy: boolean;
  actions: MemberActions;
}) {
  const [adding, setAdding] = useState(false);
  const [grant, setGrant] = useState<GrantInput | null>(null);
  const active = member.status === "ACTIVE";

  return (
    <tr>
      <td>
        {member.email}
        {member.owns_tenant && <span className="badge">Owner</span>}
      </td>
      <td>{member.status === "ACTIVE" ? "Active" : "Suspended"}</td>
      <td>
        <ul className="chips">
          {member.owns_tenant && <li className="chip muted">Owner access, from the owner pointer</li>}
          {member.grants.map((held) => (
            <li key={held.id} className="chip">
              {held.display_name}
              {scopeLabel(held)}
              {canAssign && (
                <button
                  type="button"
                  className="link"
                  aria-label={`Revoke ${held.display_name} from ${member.email}`}
                  disabled={busy}
                  onClick={() => actions.revokeGrant(member, held)}
                >
                  ×
                </button>
              )}
            </li>
          ))}
        </ul>
        {canAssign && active && sets.length > 0 && !adding && (
          <button type="button" className="link" onClick={() => setAdding(true)}>
            Add a grant
          </button>
        )}
        {adding && (
          <form
            className="form inline"
            onSubmit={(event) => {
              event.preventDefault();
              if (!grant) return;
              actions.addGrant(member, grant);
              setAdding(false);
            }}
          >
            <GrantFields sets={sets} onChange={setGrant} />
            <span className="actions">
              <button type="submit" disabled={busy || !grant}>
                Add grant
              </button>
              <button type="button" className="secondary" onClick={() => setAdding(false)}>
                Cancel
              </button>
            </span>
          </form>
        )}
      </td>
      {(canAssign || canRemove) && (
        <td>
          {canRemove && !member.owns_tenant && (
            <span className="actions">
              {active ? (
                <ConfirmButton
                  label="Suspend"
                  disabled={busy}
                  onConfirm={() => actions.suspend(member)}
                />
              ) : (
                <button
                  type="button"
                  className="secondary"
                  disabled={busy}
                  onClick={() => actions.reinstate(member)}
                >
                  Reinstate
                </button>
              )}
              <ConfirmButton
                label="Remove"
                disabled={busy}
                onConfirm={() => actions.remove(member)}
              />
            </span>
          )}
        </td>
      )}
    </tr>
  );
}
