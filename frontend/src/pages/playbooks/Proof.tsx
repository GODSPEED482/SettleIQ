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

type Capture = { capture_id: string; captured_at: string };

const isCapture = (value: unknown): value is Capture =>
  typeof value === "object" && value !== null && "capture_id" in value;

// The proof the REQUEST-stage decision recorded from the case evidence, so an approver decides
// on what the server checked, not on the agent's text.
export function Proof({ request }: { request: PlaybookRequest }) {
  const proof = proofOf(request);
  if (Object.keys(proof).length === 0) return null;
  if (isCapture(proof.original) && isCapture(proof.duplicate)) {
    return <DuplicateProof request={request} proof={proof} />;
  }
  return (
    <section className="evidence-proof" aria-label="Request proof">
      <h3>Proof</h3>
      <Facts values={proof} />
    </section>
  );
}

type DuplicateProps = { request: PlaybookRequest; proof: Record<string, unknown> };

// PB-002 (task 5.16): two distinct COMPLETED captures for one merchant order, the later one the
// duplicate. Equal amounts alone never qualify (AC-PLY-02), so both captures, the order linkage
// and the exact refund are shown, and the refund target is checked against the duplicate.
function DuplicateProof({ request, proof }: DuplicateProps) {
  const original = proof.original as Capture;
  const duplicate = proof.duplicate as Capture;
  const target = request.target;
  const aimed = target.capture_id === duplicate.capture_id;
  return (
    <section className="evidence-proof" aria-label="Duplicate capture proof">
      <h3>Duplicate capture proof</h3>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Capture</th>
              <th>ID</th>
              <th>Captured at</th>
              <th>Proposed</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>Original</td>
              <td>{original.capture_id}</td>
              <td>{new Date(original.captured_at).toLocaleString()}</td>
              <td>Keep</td>
            </tr>
            <tr>
              <td>Duplicate</td>
              <td>{duplicate.capture_id}</td>
              <td>{new Date(duplicate.captured_at).toLocaleString()}</td>
              <td>Refund</td>
            </tr>
          </tbody>
        </table>
      </div>
      <dl className="facts">
        <dt>Order reference</dt>
        <dd>{String(proof.order_reference ?? "—")}</dd>
        <dt>Refund</dt>
        <dd>
          {String(target.amount ?? "—")} {String(target.currency ?? "")}
        </dd>
        <dt>Rule</dt>
        <dd>{String(proof.rule ?? "—")}</dd>
      </dl>
      <p className="muted">
        No refund or dispute existed on either capture when the request was checked. Before
        refunding, the Worker reads both captures again and sends nothing unless both are still
        COMPLETED and the duplicate still holds exactly this amount.
      </p>
      {!aimed && (
        <p className="error" role="alert">
          The refund target does not name the duplicate capture. Do not approve.
        </p>
      )}
    </section>
  );
}
