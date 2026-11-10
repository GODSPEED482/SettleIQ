import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";

import { apiFetch } from "../../api/client";
import { useSession } from "../../session/SessionContext";
import { AuthLayout } from "./AuthLayout";
import { ResendVerification } from "./ResendVerification";

type State = "verifying" | "verified" | "failed";

// The link carries the token in the fragment (#token=...), so it never reaches server logs.
export function tokenFromHash(hash: string): string | null {
  return new URLSearchParams(hash.replace(/^#/, "")).get("token");
}

export function VerifyEmail() {
  const { refresh } = useSession();
  const location = useLocation();
  const navigate = useNavigate();
  const [state, setState] = useState<State>("verifying");
  // Tokens are single-use: StrictMode's second effect run must not spend it again.
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const token = tokenFromHash(location.hash);
    // Drop the token from the address bar and history once it has been read.
    navigate(location.pathname, { replace: true });
    if (!token) {
      setState("failed");
      return;
    }
    apiFetch("/api/auth/verify-email", { method: "POST", json: { token } })
      .then(async () => {
        setState("verified");
        await refresh();
      })
      .catch(() => setState("failed"));
  }, [location.hash, location.pathname, navigate, refresh]);

  if (state === "verifying") {
    return (
      <AuthLayout title="Verifying your email">
        <p className="muted">One moment.</p>
      </AuthLayout>
    );
  }
  if (state === "verified") {
    return (
      <AuthLayout title="Email verified">
        <p>Your email is verified.</p>
        <p>
          <Link to="/">Continue to SettleIQ</Link>
        </p>
      </AuthLayout>
    );
  }
  return (
    <AuthLayout title="This link has expired">
      <p>This link is invalid or has expired. Each link works once.</p>
      <ResendVerification />
      <p className="muted">
        <Link to="/sign-in">Back to sign in</Link>
      </p>
    </AuthLayout>
  );
}
