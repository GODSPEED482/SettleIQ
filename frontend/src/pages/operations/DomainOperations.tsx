import { useEffect, useMemo, useState } from "react";

import { messageOf } from "../../api/client";
import { can } from "../../permissions/can";
import { usePermissions } from "../../permissions/PermissionsContext";
import { operationsApi } from "./api";
import { Cases } from "./Cases";
import { Entities } from "./Entities";
import { IdentityReview } from "./IdentityReview";
import { Runs } from "./Runs";
import type { TeamMember } from "./types";

// Domain Operations (spec 13.1): runs, cases, entities and identity review. Each section loads
// only with its read key and each action shows only with its own key from
// GET /api/me/permissions; the API rechecks every call.
export function DomainOperations() {
  const { tenantId, permissions } = usePermissions();
  const canReadCases = can(permissions, "case.read");
  const canTriage = can(permissions, "case.triage");
  const canReadEntities = can(permissions, "entity.read");
  const canReadDecisions = can(permissions, "entity_resolution.read");
  const canReview = can(permissions, "entity_resolution.review");
  const canStart = can(permissions, "domain_run.create");
  const canReadMembers = can(permissions, "member.read");

  const api = useMemo(() => (tenantId ? operationsApi(tenantId) : null), [tenantId]);
  const [members, setMembers] = useState<TeamMember[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [version, setVersion] = useState(0);

  // Assignees are picked from the team, so the picker needs member.read as well.
  useEffect(() => {
    if (!api || !canTriage || !canReadMembers) return;
    let current = true;
    api
      .members()
      .then((list) => current && setMembers(list))
      .catch((caught) => current && setError(messageOf(caught)));
    return () => {
      current = false;
    };
  }, [api, canTriage, canReadMembers]);

  if (!api) return null;

  // Runs one change, reports it, then re-reads every section.
  async function run(work: () => Promise<unknown>, done: string) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await work();
      setNotice(done);
      return true;
    } catch (caught) {
      setError(messageOf(caught));
      return false;
    } finally {
      setBusy(false);
      setVersion((current) => current + 1);
    }
  }

  return (
    <section className="operations">
      <h1>Domain Operations</h1>
      <p className="muted">
        Runs, cases, entities with their observations and resolved values, and identity review.
        Reference data is labelled SYNTHETIC/REFERENCE wherever it appears.
      </p>
      {error && <p className="error" role="alert">{error}</p>}
      {notice && <p className="notice" role="status">{notice}</p>}
      <Runs
        api={api}
        version={version}
        canStart={canStart}
        busy={busy}
        onStart={() => void run(() => api.startRun(), "Run finished.")}
      />
      {canReadCases && (
        <Cases
          api={api}
          version={version}
          canTriage={canTriage}
          members={members}
          busy={busy}
          onTriage={(item, body) =>
            run(() => api.triage(item.id, body), `${item.case_type} case updated.`)
          }
          onReopen={(item, reason) =>
            run(() => api.reopen(item.id, reason), `${item.case_type} case reopened.`)
          }
        />
      )}
      {canReadEntities && <Entities api={api} version={version} />}
      {canReadDecisions && (
        <IdentityReview
          api={api}
          version={version}
          canReview={canReview}
          busy={busy}
          onDecide={(decision, body) =>
            run(
              () => api.decide(decision.id, body),
              body.outcome === "ACCEPT" ? "Identity link accepted." : "Identity link rejected.",
            )
          }
        />
      )}
    </section>
  );
}
