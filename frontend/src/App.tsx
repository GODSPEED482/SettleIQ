import { useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";

import { checkHealth } from "./api/client";
import { TenantPicker } from "./components/TenantPicker";
import { AcceptInvitation } from "./pages/AcceptInvitation";
import { SignIn } from "./pages/auth/SignIn";
import { SignUp } from "./pages/auth/SignUp";
import { VerifyEmail } from "./pages/auth/VerifyEmail";
import { CreateTenant } from "./pages/CreateTenant";
import { Investigations } from "./pages/investigations/Investigations";
import { OntologyStudio } from "./pages/OntologyStudio";
import { DomainOperations } from "./pages/operations/DomainOperations";
import { SourceManagement } from "./pages/sources/SourceManagement";
import { TeamManagement } from "./pages/team/TeamManagement";
import { WORKSPACES } from "./pages/workspaces";
import { Workspace } from "./pages/Workspace";
import { canAny } from "./permissions/can";
import { usePermissions } from "./permissions/PermissionsContext";
import { RequirePermission } from "./permissions/RequirePermission";
import { RequireSession } from "./session/RequireSession";
import { useSession } from "./session/SessionContext";

export function App() {
  return (
    <Routes>
      <Route path="/sign-in" element={<SignIn />} />
      <Route path="/sign-up" element={<SignUp />} />
      <Route path="/verify-email" element={<VerifyEmail />} />
      <Route
        path="/invitations/accept"
        element={
          <RequireSession>
            <AcceptInvitation />
          </RequireSession>
        }
      />
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
  const { signOut, refresh, tenants } = useSession();
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
        <TenantPicker />
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
          <Route
            path="/"
            element={<Navigate to={tenants.length ? WORKSPACES[0].path : "/tenants/new"} replace />}
          />
          <Route path="/tenants/new" element={<CreateTenant />} />
          {WORKSPACES.map((workspace) => (
            <Route
              key={workspace.path}
              path={workspace.path}
              element={
                <RequirePermission permission={workspace.permission}>
                  {workspace.path === "/team" ? (
                    <TeamManagement />
                  ) : workspace.path === "/ontology" ? (
                    <OntologyStudio workspace={workspace} />
                  ) : workspace.path === "/operations" ? (
                    <DomainOperations />
                  ) : workspace.path === "/sources" ? (
                    <SourceManagement />
                  ) : workspace.path === "/investigations" ? (
                    <Investigations />
                  ) : (
                    // TODO(G5 5.11 Suprava): replace the /playbooks placeholder: requests with
                    // their lifecycle state, the REQUEST- and EXECUTION-stage decisions, the
                    // approval if any, the execution, its attempts, readback verification and
                    // audit links. Approve and reject are gated on playbook.approve and
                    // playbook.reject, and hidden from the requester (POL-01). Show the exact
                    // scope's mode (SHADOW or LIVE) beside the provider environment (Sandbox).
                    // TODO(G5 5.16 Suprava): PB-002 request and approval views show the duplicate
                    // proof: both capture IDs, the order linkage, currency and amount, and that
                    // no refund or hold exists, so an approver decides on evidence.
                    <Workspace workspace={workspace} />
                  )}
                </RequirePermission>
              }
            />
          ))}
        </Routes>
      </main>
    </div>
  );
}
