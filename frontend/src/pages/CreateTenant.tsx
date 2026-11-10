import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { apiFetch, messageOf } from "../api/client";
import { usePermissions } from "../permissions/PermissionsContext";
import { useSession } from "../session/SessionContext";

// The creator becomes the owner, and the server sets that pointer atomically (AC-IAM-01). The
// body names the business only: the client never chooses a tenant ID or an owner.
export function CreateTenant() {
  const { refresh } = useSession();
  const { setTenantId } = usePermissions();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const created = await apiFetch<{ id: string }>("/api/tenants", {
        method: "POST",
        json: { name },
      });
      await refresh();
      setTenantId(created.id);
      navigate("/team");
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <h1>Create a tenant</h1>
      <p className="muted">A tenant holds one business: its team, sources and operations.</p>
      <form className="form narrow" onSubmit={submit}>
        <label>
          Business name
          <input
            required
            maxLength={200}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        {error && <p className="error" role="alert">{error}</p>}
        <button type="submit" disabled={busy}>
          Create tenant
        </button>
      </form>
    </section>
  );
}
