import { useEffect, useState } from "react";

import { apiFetch, messageOf } from "../../api/client";
import { can } from "../../permissions/can";
import { usePermissions } from "../../permissions/PermissionsContext";
import { useSession } from "../../session/SessionContext";
import { Invitations, InviteForm } from "./Invitations";
import { Members, type MemberActions } from "./Members";
import { OwnershipTransfer } from "./OwnershipTransfer";
import {
  grantableSets,
  type GrantInput,
  type Invitation,
  type Member,
  type PermissionSet,
} from "./types";

// Team Management (spec 13.1). Each control shows only with its own key from
// GET /api/me/permissions; the API rechecks every call.
export function TeamManagement() {
  const { tenantId, permissions, reload: reloadPermissions } = usePermissions();
  const { refresh: refreshSession } = useSession();
  const canInvite = can(permissions, "member.invite");
  const canManageInvitations = can(permissions, "member.invitation.manage");
  const canAssign = can(permissions, "member.role.assign");
  const canRemove = can(permissions, "member.remove");
  const canTransfer = can(permissions, "tenant.ownership.transfer");
  const canReadSets = can(permissions, "role.read");

  const [members, setMembers] = useState<Member[]>([]);
  const [invitations, setInvitations] = useState<Invitation[]>([]);
  const [sets, setSets] = useState<PermissionSet[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [version, setVersion] = useState(0);
  const base = `/api/tenants/${tenantId}`;

  useEffect(() => {
    if (!tenantId) return;
    let current = true;
    const load = async () => {
      const [memberList, invitationList, setList] = await Promise.all([
        apiFetch<Member[]>(`${base}/members`),
        canManageInvitations ? apiFetch<Invitation[]>(`${base}/invitations`) : [],
        canReadSets ? apiFetch<PermissionSet[]>("/api/permission-sets", { tenantId }) : [],
      ]);
      if (!current) return;
      setMembers(memberList);
      setInvitations(invitationList);
      setSets(grantableSets(setList));
    };
    load().catch((caught) => current && setError(messageOf(caught)));
    return () => {
      current = false;
    };
  }, [tenantId, base, version, canManageInvitations, canReadSets]);

  if (!tenantId) return null;

  // Runs one change, reports it, then re-reads the team and the caller's own permissions, since
  // a change can affect the caller too.
  async function run(work: () => Promise<unknown>, done: string, membership = false) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await work();
      setNotice(done);
      return true;
    } catch (caught) {
      setError(messageOf(caught));
      return false;
    } finally {
      setBusy(false);
      setVersion((current) => current + 1);
      reloadPermissions();
      if (membership) void refreshSession();
    }
  }

  const memberActions: MemberActions = {
    addGrant: (member, grant) =>
      void run(
        () => apiFetch(`${base}/members/${member.user_id}/grants`, {
          method: "PATCH",
          json: { add: [grant] },
        }),
        `Grant added for ${member.email}.`,
      ),
    revokeGrant: (member, grant) =>
      void run(
        () => apiFetch(`${base}/members/${member.user_id}/grants`, {
          method: "PATCH",
          json: { revoke: [grant.id] },
        }),
        `${grant.display_name} revoked from ${member.email}.`,
      ),
    suspend: (member) =>
      void run(
        () => apiFetch(`${base}/members/${member.user_id}/suspend`, { method: "POST" }),
        `${member.email} is suspended.`,
        true,
      ),
    reinstate: (member) =>
      void run(
        () => apiFetch(`${base}/members/${member.user_id}/reinstate`, { method: "POST" }),
        `${member.email} is reinstated.`,
        true,
      ),
    remove: (member) =>
      void run(
        () => apiFetch(`${base}/members/${member.user_id}`, { method: "DELETE" }),
        `${member.email} was removed.`,
        true,
      ),
  };

  async function invite(email: string, grant: GrantInput): Promise<boolean> {
    let delivered = true;
    const ok = await run(async () => {
      const created = await apiFetch<{ delivery_status: string }>(`${base}/invitations`, {
        method: "POST",
        json: { email, grants: [grant] },
      });
      delivered = created.delivery_status !== "FAILED";
    }, `Invitation sent to ${email}.`);
    // AC-IAM-09: a failed delivery is visible, and nobody joins until the invitation is accepted.
    if (ok && !delivered) {
      setNotice(`Invitation created, but the email to ${email} was not delivered. Resend it.`);
    }
    return ok;
  }

  const needsSets = (canInvite || canAssign) && !canReadSets;

  return (
    <section className="team">
      <h1>Team</h1>
      <p className="muted">Members, grants, invitations and ownership for this tenant.</p>
      {error && <p className="error" role="alert">{error}</p>}
      {notice && <p className="notice" role="status">{notice}</p>}
      {needsSets && (
        <p className="muted">
          Choosing a permission set needs <code>role.read</code>, so grants cannot be picked here.
        </p>
      )}
      <Members
        members={members}
        sets={sets}
        canAssign={canAssign}
        canRemove={canRemove}
        busy={busy}
        actions={memberActions}
      />
      {canInvite && sets.length > 0 && <InviteForm sets={sets} busy={busy} onInvite={invite} />}
      {canManageInvitations && (
        <Invitations
          invitations={invitations}
          busy={busy}
          onResend={(invitation) =>
            void run(
              () => apiFetch(`${base}/invitations/${invitation.id}/resend`, { method: "POST" }),
              `A new link went to ${invitation.email}; the old one no longer works.`,
            )
          }
          onRevoke={(invitation) =>
            void run(
              () => apiFetch(`${base}/invitations/${invitation.id}/revoke`, { method: "POST" }),
              `The invitation to ${invitation.email} is revoked.`,
            )
          }
        />
      )}
      {canTransfer && (
        <OwnershipTransfer
          members={members}
          busy={busy}
          onTransfer={(member) =>
            void run(
              () => apiFetch(`${base}/ownership-transfer`, {
                method: "POST",
                json: { new_owner_user_id: member.user_id },
              }),
              `${member.email} now owns this tenant.`,
            )
          }
        />
      )}
    </section>
  );
}
