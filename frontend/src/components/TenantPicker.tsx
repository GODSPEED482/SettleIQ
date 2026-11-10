import { Link } from "react-router-dom";

import { usePermissions } from "../permissions/PermissionsContext";
import { useSession } from "../session/SessionContext";

// Picking a tenant only selects it (spec 11.1); the API proves membership on every request.
export function TenantPicker() {
  const { tenants } = useSession();
  const { tenantId, setTenantId } = usePermissions();

  return (
    <div className="tenant-picker">
      {tenants.length > 0 && (
        <label>
          <span className="hint">Tenant</span>
          <select
            value={tenantId ?? ""}
            onChange={(event) => setTenantId(event.target.value || null)}
          >
            <option value="" disabled>
              Choose a tenant
            </option>
            {tenants.map((tenant) => (
              <option key={tenant.id} value={tenant.id}>
                {tenant.name}
              </option>
            ))}
          </select>
        </label>
      )}
      <Link to="/tenants/new">Create a tenant</Link>
    </div>
  );
}
