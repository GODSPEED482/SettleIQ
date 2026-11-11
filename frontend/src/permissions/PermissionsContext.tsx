import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
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
  // Re-reads permissions, for after a change that can alter the caller's own access.
  reload: () => void;
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
  const [version, setVersion] = useState(0);
  const reload = useCallback(() => setVersion((current) => current + 1), []);

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
  // The status of the last load and the tenant it was for. A tenant selected since then has
  // not been asked about yet, so it reads as loading rather than as holding nothing.
  const [loaded, setLoaded] = useState<{ tenantId: string | null; status: Status }>({
    tenantId: null,
    status: "no-tenant",
  });
  const loadedTenant = useRef<string | null>(null);

  useEffect(() => {
    // A new tenant starts from nothing. A reload keeps the current permissions on screen until
    // the fresh ones arrive, so the workspace in use is not unmounted.
    const switched = loadedTenant.current !== tenantId;
    loadedTenant.current = tenantId;
    if (switched) setPermissions([]);
    if (!tenantId) {
      setLoaded({ tenantId, status: "no-tenant" });
      return;
    }
    let current = true;
    if (switched) setLoaded({ tenantId, status: "loading" });
    apiFetch<{ permissions: EffectivePermission[] }>(
      `/api/me/permissions?tenant_id=${encodeURIComponent(tenantId)}`,
    )
      .then((body) => {
        if (!current) return;
        setPermissions(body.permissions);
        setLoaded({ tenantId, status: "ready" });
      })
      .catch((error) => {
        if (!current) return;
        const signedOut = error instanceof ApiError && error.status === 401;
        setLoaded({ tenantId, status: signedOut ? "signed-out" : "unavailable" });
      });
    return () => {
      current = false;
    };
  }, [tenantId, version]);

  const status: Status = !tenantId
    ? "no-tenant"
    : loaded.tenantId === tenantId
      ? loaded.status
      : "loading";

  const value = useMemo(
    () => ({ tenantId, setTenantId, reload, permissions, status }),
    [tenantId, setTenantId, reload, permissions, status],
  );
  return <PermissionsContext.Provider value={value}>{children}</PermissionsContext.Provider>;
}

export function usePermissions(): PermissionsState {
  const value = useContext(PermissionsContext);
  if (!value) throw new Error("usePermissions must be used inside PermissionsProvider");
  return value;
}
