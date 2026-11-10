import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";

import { AuthLayout } from "../pages/auth/AuthLayout";
import { ResendVerification } from "../pages/auth/ResendVerification";
import { useSession } from "./SessionContext";

// Gates the app shell on a verified session. The API enforces this on every route regardless.
export function RequireSession({ children }: { children: ReactNode }) {
  const { status, refresh, signOut } = useSession();
  const location = useLocation();

  if (status === "loading") {
    return <p className="muted loading">Loading…</p>;
  }
  if (status === "signed-out") {
    const from = location.pathname + location.search + location.hash;
    return <Navigate to="/sign-in" replace state={{ from }} />;
  }
  if (status === "unverified") {
    return (
      <AuthLayout title="Verify your email">
        <p>Open the link we emailed you to verify your address, then continue.</p>
        <ResendVerification />
        <div className="actions">
          <button type="button" onClick={() => void refresh()}>
            I have verified
          </button>
          <button type="button" className="secondary" onClick={() => void signOut()}>
            Sign out
          </button>
        </div>
      </AuthLayout>
    );
  }
  if (status === "unavailable") {
    return (
      <AuthLayout title="SettleIQ is unreachable">
        <p>The API did not answer. Check your connection and try again.</p>
        <button type="button" onClick={() => void refresh()}>
          Try again
        </button>
      </AuthLayout>
    );
  }
  return <>{children}</>;
}
