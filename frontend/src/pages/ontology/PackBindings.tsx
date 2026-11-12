import type { PackBindingSet } from "./packHints";

// The bindings and authority assertions an active pack ships for a connector the tenant uses,
// offered per source. Nothing is applied until a person applies it.
export function PackBindings({
  sets,
  canEditMappings,
  canEditAuthority,
  busy,
  onApply,
}: {
  sets: PackBindingSet[];
  canEditMappings: boolean;
  canEditAuthority: boolean;
  busy: boolean;
  onApply: (set: PackBindingSet) => void;
}) {
  if (sets.length === 0) return null;
  return (
    <section className="panel" aria-label="Pack bindings">
      <h2>Pack bindings</h2>
      <p className="muted">
        Bindings an active domain pack provides for a connector you use. They are added to this
        draft like any other binding and published only with it.
      </p>
      <ul>
        {sets.map((set) => {
          const mappings = canEditMappings ? set.mappings.length : 0;
          const authority = canEditAuthority ? set.authority.length : 0;
          return (
            <li key={`${set.namespace}:${set.source.id}`}>
              {set.namespace} for {set.source.display_name}: {set.mappings.length} bindings,{" "}
              {set.authority.length} authority assertions{" "}
              {mappings + authority > 0 && (
                <button type="button" className="secondary" disabled={busy} onClick={() => onApply(set)}>
                  Apply {set.namespace} bindings to {set.source.display_name}
                </button>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
