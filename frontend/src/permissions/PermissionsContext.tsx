import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { ApiError, apiFetch } from "../api/client";
import { useSession } from "../session/SessionContext";
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

// The last tenant picked, per browser. A convenience only: it is checked against the session's
// tenants before use, and membership is proved by the server on every request.
const TENANT_KEY = "settleiq.tenant";

function rememberedTenant(): string | null {
  try {
    return localStorage.getItem(TENANT_KEY);
  } catch {
    return null;
  }
}

function rememberTenant(tenantId: string | null): void {
  try {
    if (tenantId) localStorage.setItem(TENANT_KEY, tenantId);
    else localStorage.removeItem(TENANT_KEY);
  } catch {
    // Storage can be blocked; the picker still works for this page load.
  }
}

export function PermissionsProvider({ children }: { children: ReactNode }) {
  const session = useSession();
  const [chosen, setChosen] = useState<string | null>(rememberedTenant);
  const [permissions, setPermissions] = useState<EffectivePermission[]>([]);

  // Only a tenant the session lists can be selected. With one tenant, it is selected for you.
  const listed = (id: string | null) => !!id && session.tenants.some((tenant) => tenant.id === id);
  const tenantId =
    session.status !== "signed-in"
      ? null
      : listed(chosen)
        ? chosen
        : session.tenants.length === 1
          ? session.tenants[0].id
          : null;

  const setTenantId = useCallback((id: string | null) => {
    setChosen(id);
    rememberTenant(id);
  }, []);

  useEffect(() => {
    if (session.status === "signed-out") setTenantId(null);
  }, [session.status, setTenantId]);
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
    [tenantId, setTenantId, permissions, status],
  );
  return <PermissionsContext.Provider value={value}>{children}</PermissionsContext.Provider>;
}

export function usePermissions(): PermissionsState {
  const value = useContext(PermissionsContext);
  if (!value) throw new Error("usePermissions must be used inside PermissionsProvider");
  return value;
}
