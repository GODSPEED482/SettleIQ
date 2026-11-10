// Response shapes of the Team routes (backend/app/tenancy/members.py and invitations.py).

export type Grant = {
  id: string;
  permission_set_key: string;
  display_name: string;
  scope_type: string | null;
  scope_id: string | null;
};

export type Member = {
  user_id: string;
  email: string;
  status: "ACTIVE" | "SUSPENDED";
  joined_at: string | null;
  owns_tenant: boolean;
  grants: Grant[];
};

export type Invitation = {
  id: string;
  email: string;
  status: string;
  expires_at: string;
  delivery_status: string;
  delivery_attempts: number;
  last_delivery_error: string | null;
  created_at: string;
};

export type PermissionSet = {
  key: string;
  display_name: string;
  principal_type: string;
  derivation: string | null;
};

export type GrantInput = {
  permission_set_key: string;
  scope_type?: string;
  scope_id?: string;
};

// Sets a person can be granted: human, and not derived (the owner set comes from the pointer).
// The server still decides which of these the caller may grant (POL-02, POL-13).
export function grantableSets(sets: PermissionSet[]): PermissionSet[] {
  return sets.filter((set) => set.principal_type === "human" && set.derivation === null);
}
