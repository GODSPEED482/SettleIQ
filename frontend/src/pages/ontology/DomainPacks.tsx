import type { DomainPack } from "./types";

// The tenant's domain packs (spec 5.1, OPEN-03). Activation composes a pack into the next draft,
// so an open draft started before a change still lacks it, and generation can only propose terms
// the draft composes; the panel says so rather than letting proposals come back incomplete.
export function DomainPacks({
  packs,
  draftPacks,
  canManage,
  busy,
  onActivate,
  onDisable,
}: {
  packs: DomainPack[];
  draftPacks: string[] | null;
  canManage: boolean;
  busy: boolean;
  onActivate: (namespace: string) => void;
  onDisable: (namespace: string) => void;
}) {
  const active = packs.filter((pack) => pack.status === "ACTIVE").map((pack) => pack.namespace);
  const missing = draftPacks ? active.filter((namespace) => !draftPacks.includes(namespace)) : [];
  const stale = draftPacks ? draftPacks.filter((namespace) => !active.includes(namespace)) : [];

  return (
    <section className="panel" aria-label="Domain packs">
      <h2>Domain packs</h2>
      <p className="muted">
        Active packs make up the ontology: their classes are what sources bind to and what each
        pack's run evaluates. Execution modes stay SHADOW until switched under Playbook Requests.
      </p>
      {packs.length === 0 ? (
        <p className="muted">No domain packs are installed on this platform.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Pack</th>
                <th>Version</th>
                <th>Finds</th>
                <th>Status</th>
                {canManage && <th aria-label="Actions" />}
              </tr>
            </thead>
            <tbody>
              {packs.map((pack) => (
                <tr key={pack.namespace}>
                  <td>
                    <code>{pack.namespace}</code>
                  </td>
                  <td>{pack.version}</td>
                  <td>
                    {pack.case_types.length === 0 ? (
                      <span className="muted">No case types</span>
                    ) : (
                      <ul className="chips">
                        {pack.case_types.map((code) => (
                          <li key={code} className="chip">
                            {code}
                          </li>
                        ))}
                      </ul>
                    )}
                  </td>
                  <td>{pack.status === "ACTIVE" ? "Active" : pack.status === "DISABLED" ? "Disabled" : "Available"}</td>
                  {canManage && (
                    <td>
                      {pack.status === "ACTIVE" ? (
                        <button
                          type="button"
                          className="secondary"
                          disabled={busy}
                          onClick={() => onDisable(pack.namespace)}
                        >
                          Disable {pack.namespace}
                        </button>
                      ) : (
                        <button type="button" disabled={busy} onClick={() => onActivate(pack.namespace)}>
                          Activate {pack.namespace}
                        </button>
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {(missing.length > 0 || stale.length > 0) && (
        <p className="notice" role="status">
          The open draft was started with a different set of packs
          {missing.length > 0 && <> and does not include {missing.join(", ")}</>}
          {stale.length > 0 && <>{missing.length > 0 ? ";" : " and"} still includes {stale.join(", ")}</>}. Abandon it
          and start a new draft before generating proposals.
        </p>
      )}
    </section>
  );
}
