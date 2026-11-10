import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";

import { apiFetch } from "../api/client";
import { usePermissions } from "../permissions/PermissionsContext";
import { useSession } from "../session/SessionContext";
import { AuthLayout } from "./auth/AuthLayout";
import { tokenFromHash } from "./auth/VerifyEmail";

type State = "ready" | "accepting" | "failed";

// POST /api/invitations/{token}/accept (AC-IAM-03). The session gate sends a signed-out visitor
// to sign in and back here with the fragment intact. Joining takes an explicit click.
export function AcceptInvitation() {
  const { refresh } = useSession();
  const { setTenantId } = usePermissions();
  const location = useLocation();
  const navigate = useNavigate();
  // undefined until the fragment is read; null when it carried no token.
  const [token, setToken] = useState<string | null | undefined>(undefined);
  const [state, setState] = useState<State>("ready");
  const read = useRef(false);

  useEffect(() => {
    if (read.current) return;
    read.current = true;
    setToken(tokenFromHash(location.hash));
    // Drop the token from the address bar and history once it has been read.
    navigate(location.pathname, { replace: true });
  }, [location.hash, location.pathname, navigate]);

  async function accept() {
    if (!token) return;
    setState("accepting");
    try {
      const joined = await apiFetch<{ tenant_id: string }>(
        `/api/invitations/${encodeURIComponent(token)}/accept`,
        { method: "POST" },
      );
      await refresh();
      setTenantId(joined.tenant_id);
      navigate("/team", { replace: true });
    } catch {
      setState("failed");
    }
  }

  // The API gives one answer for every refusal, so this page does too.
  if (state === "failed" || token === null) {
    return (
      <AuthLayout title="Invitation not accepted">
        <p>
          This invitation is invalid, has expired, was already used, or was sent to a different
          email address. Ask the person who invited you to send a new one.
        </p>
        <p>
          <Link to="/">Continue to SettleIQ</Link>
        </p>
      </AuthLayout>
    );
  }
  return (
    <AuthLayout title="Join a team">
      <p>
        You have been invited to a SettleIQ tenant. The invitation must match the email you are
        signed in with, and that email must be verified.
      </p>
      <button type="button" disabled={!token || state === "accepting"} onClick={accept}>
        Accept invitation
      </button>
    </AuthLayout>
  );
}
