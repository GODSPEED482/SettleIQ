import { describe, expect, it } from "vitest";

import { can, type EffectivePermission } from "./can";

const source = (id: string) => ({ type: "source", id });

const permissions: EffectivePermission[] = [
  { key: "tenant.read", tenant_wide: true, scopes: [], denied_scopes: [] },
  { key: "source.sync", tenant_wide: false, scopes: [source("a")], denied_scopes: [] },
  { key: "source.read", tenant_wide: true, scopes: [], denied_scopes: [source("x")] },
];

describe("can", () => {
  it("denies keys the user does not hold", () => {
    expect(can(permissions, "playbook.approve")).toBe(false);
  });

  it("allows tenant-wide permissions", () => {
    expect(can(permissions, "tenant.read")).toBe(true);
  });

  it("limits a narrowed permission to its scope", () => {
    expect(can(permissions, "source.sync", source("a"))).toBe(true);
    expect(can(permissions, "source.sync", source("b"))).toBe(false);
    expect(can(permissions, "source.sync")).toBe(false);
  });

  it("lets a scoped deny override a tenant-wide allow", () => {
    expect(can(permissions, "source.read", source("x"))).toBe(false);
    expect(can(permissions, "source.read", source("y"))).toBe(true);
  });
});
