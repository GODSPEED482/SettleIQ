import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { apiFetch } from "../api/client";
import type { EffectivePermission } from "./can";

type Status = "no-tenant" | "loading" | "ready" | "unavailable";

type PermissionsState = {
  tenantId: string | null;
  setTenantId: (tenantId: string | null) => void;
  permissions: EffectivePermission[];
  status: Status;
};

const PermissionsContext = createContext<PermissionsState | null>(null);

export function PermissionsProvider({ children }: { children: ReactNode }) {
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
      .catch(() => {
        if (current) setStatus("unavailable");
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
