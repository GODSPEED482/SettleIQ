import { useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";

import { checkHealth } from "./api/client";
import { OntologyStudio } from "./pages/OntologyStudio";
import { WORKSPACES } from "./pages/workspaces";
import { Workspace } from "./pages/Workspace";
import { canAny } from "./permissions/can";
import { usePermissions } from "./permissions/PermissionsContext";

// TODO(G1 1.11 Suprava): add sign-up, sign-in, email-verification and tenant-creation routes.
// TODO(G1 1.11 Suprava): add a tenant picker. Nothing calls setTenantId yet, so all nav is locked.
// TODO(G1 1.11 Suprava): locked links still navigate; show an unavailable state on those pages.
// TODO(G1 1.11 Suprava): the footer's "Signed in" reflects permission loading, not a session.
// TODO(G1 1.12 Suprava): add the invitation-acceptance screen (POST /invitations/{token}/accept).
export function App() {
  const { permissions, status } = usePermissions();
  const [apiUp, setApiUp] = useState<boolean | null>(null);

  useEffect(() => {
    checkHealth().then(setApiUp);
  }, []);

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
          <span>{status === "ready" ? "Signed in" : "Not signed in"}</span>
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
