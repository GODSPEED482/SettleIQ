import type { PackBindingSet, PackGap } from "./packHints";

// The bindings and authority assertions an active pack ships for a connector the tenant uses,
// offered per source, and what each active pack's run would skip with the draft's bindings.
// Nothing is applied until a person applies it.
export function PackBindings({
  sets,
  gaps,
  canEditMappings,
  canEditAuthority,
  busy,
  onApply,
}: {
  sets: PackBindingSet[];
  gaps: PackGap[];
  canEditMappings: boolean;
  canEditAuthority: boolean;
  busy: boolean;
  onApply: (set: PackBindingSet) => void;
}) {
  if (sets.length === 0 && gaps.length === 0) return null;
  return (
    <section className="panel" aria-label="Pack bindings">
      <h2>Pack bindings</h2>
      {gaps.length > 0 && (
        <div className="notice">
          <p>Each active pack's run will miss these with the bindings on this draft:</p>
          <ul>
            {gaps.map((gap, index) => (
              <li key={index}>
                <span className="badge">{gap.namespace}</span> {gap.message}
              </li>
            ))}
          </ul>
        </div>
      )}
      {sets.length > 0 && (
        <p className="muted">
          Bindings an active domain pack provides for a connector you use. They are added to this
          draft like any other binding and published only with it.
        </p>
      )}
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
