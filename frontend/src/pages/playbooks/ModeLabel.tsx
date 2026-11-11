import { environmentOf } from "./api";
import type { PlaybookRequest, Readiness } from "./types";

type Props = { request: PlaybookRequest; readiness: Readiness[] };

// The exact scope's mode (spec 9.5) beside the provider environment. SHADOW blocks every
// business-state change, whatever the approval says.
export function ModeLabel({ request, readiness }: Props) {
  const pack = request.playbook_id.split(":")[0];
  const scope = readiness.find(
    (row) =>
      row.domain_pack_id === pack &&
      row.connector_id === request.connector_id &&
      row.action_capability === request.action_capability,
  );
  return (
    <p className="mode-label">
      {scope ? (
        <span className={`status status-${scope.mode.toLowerCase()}`}>{scope.mode}</span>
      ) : (
        <span className="muted">Mode not visible</span>
      )}{" "}
      <span>{environmentOf(request.connector_id)}</span>
      {scope && !scope.release_gate.passes && (
        <span className="muted"> · release gate fails at {scope.release_gate.failing_check}</span>
      )}
    </p>
  );
}
