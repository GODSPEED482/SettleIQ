import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { ApiError, apiFetch } from "../api/client";
import type { EffectivePermission } from "./can";

// "signed-out" is a 401: sign in again. "unavailable" covers a 404, which is also what a tenant
// the user cannot see returns.
type Status = "no-tenant" | "loading" | "ready" | "signed-out" | "unavailable";

type PermissionsState = {
  tenantId: string | null;
  setTenantId: (tenantId: string | null) => void;
  permissions: EffectivePermission[];
  status: Status;
};

const PermissionsContext = createContext<PermissionsState | null>(null);

export function PermissionsProvider({ children }: { children: ReactNode }) {
  // TODO(G1 1.11 Suprava): set this from the tenant picker and clear it on sign-out.
  // Set by the sign-in and tenant-selection flow (tasks 1.9 and 1.11).
  const [tenantId, setTenantId] = useState<string | null>(null);
  const [permissions, setPermissions] = useState<EffectivePermission[]>([]);
  const [status, setStatus] = useState<Status>("no-tenant");

  useEffect(() => {
    setPermissions([]);
    if (!tenantId) {
      setStatus("no-tenant");
      return;
    }
    let current = true;
    setStatus("loading");
    apiFetch<{ permissions: EffectivePermission[] }>(
      `/api/me/permissions?tenant_id=${encodeURIComponent(tenantId)}`,
    )
      .then((body) => {
        if (!current) return;
        setPermissions(body.permissions);
        setStatus("ready");
      })
      .catch((error) => {
        if (!current) return;
        const signedOut = error instanceof ApiError && error.status === 401;
        setStatus(signedOut ? "signed-out" : "unavailable");
      });
    return () => {
      current = false;
    };
  }, [tenantId]);

  const value = useMemo(
    () => ({ tenantId, setTenantId, permissions, status }),
    [tenantId, permissions, status],
  );
  return <PermissionsContext.Provider value={value}>{children}</PermissionsContext.Provider>;
}

export function usePermissions(): PermissionsState {
  const value = useContext(PermissionsContext);
  if (!value) throw new Error("usePermissions must be used inside PermissionsProvider");
  return value;
}
