import { useState } from "react";

import type { GrantInput, PermissionSet } from "./types";

// One grant: a permission set, optionally narrowed to one scope. The server rejects a scope
// the set's permissions do not declare instead of dropping it (spec 3.3).
export function GrantFields({
  sets,
  onChange,
}: {
  sets: PermissionSet[];
  onChange: (grant: GrantInput | null) => void;
}) {
  const [setKey, setSetKey] = useState("");
  const [scopeType, setScopeType] = useState("");
  const [scopeId, setScopeId] = useState("");

  function update(next: { setKey?: string; scopeType?: string; scopeId?: string }) {
    const key = next.setKey ?? setKey;
    const type = (next.scopeType ?? scopeType).trim();
    const id = (next.scopeId ?? scopeId).trim();
    setSetKey(key);
    setScopeType(next.scopeType ?? scopeType);
    setScopeId(next.scopeId ?? scopeId);
    // Half a scope is no grant at all, never a silently broader tenant-wide one.
    if (!key || !type !== !id) return onChange(null);
    onChange(type ? { permission_set_key: key, scope_type: type, scope_id: id } : {
      permission_set_key: key,
    });
  }

  const halfScope = !scopeType.trim() !== !scopeId.trim();

  return (
    <div className="grant-fields">
      <label>
        Permission set
        <select value={setKey} required onChange={(event) => update({ setKey: event.target.value })}>
          <option value="" disabled>
            Choose a set
          </option>
          {sets.map((set) => (
            <option key={set.key} value={set.key}>
              {set.display_name}
            </option>
          ))}
        </select>
      </label>
      <label>
        Scope type <span className="hint">(optional)</span>
        <input
          value={scopeType}
          placeholder="source"
          onChange={(event) => update({ scopeType: event.target.value })}
        />
      </label>
      <label>
        Scope ID <span className="hint">(optional)</span>
        <input value={scopeId} onChange={(event) => update({ scopeId: event.target.value })} />
      </label>
      {halfScope && <p className="hint">Give both scope fields, or neither.</p>}
    </div>
  );
}
