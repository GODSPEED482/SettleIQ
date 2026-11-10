import { useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";

import { checkHealth } from "./api/client";
import { SignIn } from "./pages/auth/SignIn";
import { SignUp } from "./pages/auth/SignUp";
import { VerifyEmail } from "./pages/auth/VerifyEmail";
import { OntologyStudio } from "./pages/OntologyStudio";
import { WORKSPACES } from "./pages/workspaces";
import { Workspace } from "./pages/Workspace";
import { canAny } from "./permissions/can";
import { usePermissions } from "./permissions/PermissionsContext";
import { RequireSession } from "./session/RequireSession";
import { useSession } from "./session/SessionContext";

// TODO(G1 1.11 Suprava): add the tenant-creation route.
// TODO(G1 1.11 Suprava): add a tenant picker. Nothing calls setTenantId yet, so all nav is locked.
// TODO(G1 1.11 Suprava): locked links still navigate; show an unavailable state on those pages.
// TODO(G1 1.12 Suprava): add the invitation-acceptance screen (POST /invitations/{token}/accept).
export function App() {
  return (
    <Routes>
      <Route path="/sign-in" element={<SignIn />} />
      <Route path="/sign-up" element={<SignUp />} />
      <Route path="/verify-email" element={<VerifyEmail />} />
      <Route
        path="*"
        element={
          <RequireSession>
            <Shell />
          </RequireSession>
        }
      />
    </Routes>
  );
}

function Shell() {
  const { signOut, refresh } = useSession();
  const { permissions, status } = usePermissions();
  const [apiUp, setApiUp] = useState<boolean | null>(null);

  useEffect(() => {
    checkHealth().then(setApiUp);
  }, []);

  // A 401 while loading permissions means the session ended; let the session gate react.
  useEffect(() => {
    if (status === "signed-out") void refresh();
  }, [status, refresh]);

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">SettleIQ</div>
        <nav>
          {WORKSPACES.map((workspace) => {
            const allowed = canAny(permissions, workspace.permission);
            return (
              <NavLink
                key={workspace.path}
                to={workspace.path}
                className={allowed ? undefined : "locked"}
                title={allowed ? undefined : `Requires ${workspace.permission}`}
              >
                {workspace.title}
              </NavLink>
            );
          })}
        </nav>
        <footer className="status">
          <span>API: {apiUp === null ? "checking" : apiUp ? "up" : "unreachable"}</span>
          <span>Signed in</span>
          <button type="button" className="link" onClick={() => void signOut()}>
            Sign out
          </button>
        </footer>
      </aside>
      <main className="content">
        <Routes>
          <Route path="/" element={<Navigate to={WORKSPACES[0].path} replace />} />
          {WORKSPACES.map((workspace) => (
            <Route
              key={workspace.path}
              path={workspace.path}
              element={
                workspace.path === "/ontology" ? (
                  <OntologyStudio workspace={workspace} />
                ) : (
                  <Workspace workspace={workspace} />
                )
              }
            />
          ))}
        </Routes>
      </main>
    </div>
  );
}
