import { useEffect, useMemo, useState } from "react";

import { messageOf } from "../../api/client";
import { can, canAny } from "../../permissions/can";
import { usePermissions } from "../../permissions/PermissionsContext";
import { playbooksApi } from "../playbooks/api";
import { RequestPlaybook } from "../playbooks/RequestPlaybook";
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
  const canReadPacks = can(permissions, "domain_pack.read");
  const canReadMembers = can(permissions, "member.read");
  // The recommendation comes from the case's investigations; each playbook's own scope is
  // checked inside the request form.
  const canRequest =
    canAny(permissions, "playbook.request") && can(permissions, "investigation.read");

  const api = useMemo(() => (tenantId ? operationsApi(tenantId) : null), [tenantId]);
  const playbooks = useMemo(() => (tenantId ? playbooksApi(tenantId) : null), [tenantId]);
  const [members, setMembers] = useState<TeamMember[]>([]);
  const [packs, setPacks] = useState<string[] | null>(null);
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

  // With more than one pack active, each run names its pack, so the panel offers one per pack.
  useEffect(() => {
    if (!api || !canStart || !canReadPacks) return;
    let current = true;
    api
      .activePacks()
      .then((list) => current && setPacks(list))
      .catch((caught) => current && setError(messageOf(caught)));
    return () => {
      current = false;
    };
  }, [api, canStart, canReadPacks]);

  if (!api || !playbooks) return null;

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
        packs={packs}
        busy={busy}
        onStart={(namespace) =>
          void run(() => api.startRun(namespace), namespace ? `${namespace} run finished.` : "Run finished.")
        }
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
          canRequest={canRequest}
          renderRequest={(item) => (
            <RequestPlaybook
              key={item.id}
              api={playbooks}
              item={item}
              permissions={permissions}
              onCreated={() => setVersion((current) => current + 1)}
            />
          )}
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
