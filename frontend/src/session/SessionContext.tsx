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

export type Tenant = { id: string; name: string };

// "unverified": signed in, but tenant routes refuse the session until the email is verified.
// "unavailable": the API could not answer, so the session state is unknown.
export type SessionStatus = "loading" | "signed-out" | "unverified" | "signed-in" | "unavailable";

type SessionState = {
  status: SessionStatus;
  tenants: Tenant[];
  refresh: () => Promise<void>;
  signIn: (email: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
};

const SessionContext = createContext<SessionState | null>(null);

export function sessionStatusFor(error: unknown): SessionStatus {
  if (error instanceof ApiError) {
    if (error.status === 401) return "signed-out";
    if (error.code === "EMAIL_NOT_VERIFIED") return "unverified";
  }
  return "unavailable";
}

// The session cookie is HttpOnly (OPEN-14), so the client learns its state by asking the API.
// GET /api/me/tenants answers that and lists the tenants the picker offers.
export function SessionProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<SessionStatus>("loading");
  const [tenants, setTenants] = useState<Tenant[]>([]);

  const refresh = useCallback(async () => {
    try {
      const body = await apiFetch<{ tenants: Tenant[] }>("/api/me/tenants");
      setTenants(body.tenants);
      setStatus("signed-in");
    } catch (error) {
      setTenants([]);
      setStatus(sessionStatusFor(error));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const signIn = useCallback(
    async (email: string, password: string) => {
      await apiFetch("/api/auth/sign-in", { method: "POST", json: { email, password } });
      await refresh();
    },
    [refresh],
  );

  // The server revokes the session; refreshing afterwards reports what the server now holds.
  const signOut = useCallback(async () => {
    try {
      await apiFetch("/api/auth/sign-out", { method: "POST" });
    } finally {
      await refresh();
    }
  }, [refresh]);

  const value = useMemo(
    () => ({ status, tenants, refresh, signIn, signOut }),
    [status, tenants, refresh, signIn, signOut],
  );
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionState {
  const value = useContext(SessionContext);
  if (!value) throw new Error("useSession must be used inside SessionProvider");
  return value;
}
