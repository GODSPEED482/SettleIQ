import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { canAny } from "./can";
import { usePermissions } from "./PermissionsContext";

// Shows why a workspace is closed instead of rendering it. Hiding is not authorization: the
// API rechecks every operation (spec 12.3).
export function RequirePermission({
  permission,
  children,
}: {
  permission: string;
  children: ReactNode;
}) {
  const { tenantId, permissions, status } = usePermissions();

  if (!tenantId) {
    return (
      <section>
        <h1>Choose a tenant</h1>
        <p>
          Pick a tenant in the sidebar, or <Link to="/tenants/new">create one</Link>.
        </p>
      </section>
    );
  }
  if (status === "loading" || status === "signed-out") {
    return <p className="muted">Loading permissions…</p>;
  }
  if (status === "unavailable") {
    return (
      <section>
        <h1>Not available</h1>
        <p>Your permissions for this tenant could not be loaded. You may no longer be a member.</p>
      </section>
    );
  }
  if (!canAny(permissions, permission)) {
    return (
      <section>
        <h1>Not available</h1>
        <p>
          This workspace needs <code>{permission}</code>, which you do not hold in this tenant.
          Ask whoever manages your team&apos;s grants.
        </p>
      </section>
    );
  }
  return <>{children}</>;
}
