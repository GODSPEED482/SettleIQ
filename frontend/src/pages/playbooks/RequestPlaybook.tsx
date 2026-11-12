import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { messageOf } from "../../api/client";
import type { EffectivePermission } from "../../permissions/can";
import { StatusBadge } from "../operations/labels";
import type { DomainCase } from "../operations/types";
import { canOnPlaybook, newIdempotencyKey, type PlaybooksApi } from "./api";
import { Proof } from "./Proof";
import type { PlaybookRequest } from "./types";

type Props = {
  api: PlaybooksApi;
  item: DomainCase;
  permissions: EffectivePermission[];
  onCreated: () => void;
};

// "Create playbook request" on case review, for the playbook the case's latest finished
// investigation recommends. The body is the case alone (spec 12.1); the server decides the
// requester, approval, target and policy, and refuses an ineligible case with its reasons.
export function RequestPlaybook({ api, item, permissions, onCreated }: Props) {
  const [idempotencyKey] = useState(newIdempotencyKey);
  const [recommended, setRecommended] = useState<string | null | undefined>(undefined);
  const [created, setCreated] = useState<PlaybookRequest | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let current = true;
    api
      .investigations(item.id)
      .then((list) => {
        if (!current) return;
        const latest = list.find((found) => found.status === "SUCCEEDED");
        setRecommended(latest?.recommended_playbook ?? null);
      })
      .catch((caught) => current && setError(messageOf(caught)));
    return () => {
      current = false;
    };
  }, [api, item.id]);

  async function create(playbookId: string) {
    setBusy(true);
    setError(null);
    try {
      setCreated(await api.create(playbookId, item.id, idempotencyKey));
      onCreated();
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="triage" aria-label="Playbook request">
      <h3>Create playbook request</h3>
      {error && <p className="error" role="alert">{error}</p>}
      {recommended === null && (
        <p className="muted">
          No finished investigation recommends a playbook for this case. Investigate it first.
        </p>
      )}
      {recommended && !created && !canOnPlaybook(permissions, "playbook.request", recommended) && (
        <p className="muted">You cannot request {recommended}.</p>
      )}
      {recommended && !created && canOnPlaybook(permissions, "playbook.request", recommended) && (
        <>
          <p>
            The latest investigation recommends <strong>{recommended}</strong>. The request sends
            only this case; approval, target and policy are decided by the server.
          </p>
          <div className="actions">
            <button type="button" disabled={busy} onClick={() => void create(recommended)}>
              Create playbook request
            </button>
          </div>
        </>
      )}
      {created && (
        <>
          <p className="notice" role="status">
            Request created: <StatusBadge status={created.status} />{" "}
            <Link to={`/playbooks?request=${created.id}`}>Open the request</Link>
          </p>
          <Proof request={created} />
        </>
      )}
    </section>
  );
}
