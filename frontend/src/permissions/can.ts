// UI availability comes only from GET /api/me/permissions (spec 12.3). Hiding a control is
// not authorization: the API rechecks every operation. Never branch on permission-set names.

export type Scope = { type: string; id: string };

export type EffectivePermission = {
  key: string;
  tenant_wide: boolean;
  scopes: Scope[];
  denied_scopes: Scope[];
};

const sameScope = (a: Scope, b: Scope) => a.type === b.type && a.id === b.id;

// With no scope, only a tenant-wide holder passes. Use canAny to open a workspace, then can with
// a scope for each control inside it.
export function can(permissions: EffectivePermission[], key: string, scope?: Scope): boolean {
  const permission = permissions.find((candidate) => candidate.key === key);
  if (!permission) return false;
  if (!scope) return permission.tenant_wide;
  if (permission.denied_scopes.some((denied) => sameScope(denied, scope))) return false;
  return permission.tenant_wide || permission.scopes.some((held) => sameScope(held, scope));
}

// Whether any part of the key is held: tenant-wide, or at least one scope. Nav and workspace
// entry use this, so a scope-only holder can reach the scopes they hold.
export function canAny(permissions: EffectivePermission[], key: string): boolean {
  const permission = permissions.find((candidate) => candidate.key === key);
  return !!permission && (permission.tenant_wide || permission.scopes.length > 0);
}
