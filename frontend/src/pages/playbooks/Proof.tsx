import type { PlaybookRequest } from "./types";

const text = (value: unknown) =>
  value === null || value === undefined
    ? "—"
    : typeof value === "object"
      ? JSON.stringify(value)
      : String(value);

export function Facts({ values }: { values: Record<string, unknown> }) {
  return (
    <dl className="facts">
      {Object.entries(values).map(([key, value]) => (
        <FactRow key={key} label={key} value={text(value)} />
      ))}
    </dl>
  );
}

function FactRow({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </>
  );
}

export const proofOf = (request: PlaybookRequest) =>
  request.decisions.find((decision) => decision.stage === "REQUEST")?.conditions.proof ?? {};

// The proof the REQUEST-stage decision recorded from the case evidence, so an approver decides
// on what the server checked, not on the agent's text.
// TODO(G5 5.16 Suprava): PB-002 request and approval views show the duplicate proof: both
// capture IDs, the order linkage, currency and amount, and that no refund or hold exists, so
// an approver decides on evidence.
export function Proof({ request }: { request: PlaybookRequest }) {
  const proof = proofOf(request);
  if (Object.keys(proof).length === 0) return null;
  return (
    <section className="evidence-proof" aria-label="Request proof">
      <h3>Proof</h3>
      <Facts values={proof} />
    </section>
  );
}
