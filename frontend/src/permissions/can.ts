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

// TODO(G1 1.11 Suprava): with no scope, a scope-only holder gets false, so nav stays locked for
// them. Decide whether nav should allow any held scope.
export function can(permissions: EffectivePermission[], key: string, scope?: Scope): boolean {
  const permission = permissions.find((candidate) => candidate.key === key);
  if (!permission) return false;
  if (!scope) return permission.tenant_wide;
  if (permission.denied_scopes.some((denied) => sameScope(denied, scope))) return false;
  return permission.tenant_wide || permission.scopes.some((held) => sameScope(held, scope));
}
