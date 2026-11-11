import type { Provenance } from "./types";

// Spec 10.3.1: reference data is labelled wherever it appears, never shown as provider evidence.
export function ProvenanceLabel({ provenance }: { provenance: Provenance | null }) {
  if (provenance === "synthetic_reference") {
    return <span className="badge synthetic">SYNTHETIC/REFERENCE</span>;
  }
  return provenance ? <span className="muted">{provenance}</span> : null;
}

export function StatusBadge({ status }: { status: string }) {
  return <span className={`status status-${status.toLowerCase()}`}>{status}</span>;
}

export const shortId = (id: string) => id.slice(0, 8);

export const amount = (value: string | null, currency: string | null) =>
  value === null ? "—" : `${value} ${currency ?? ""}`.trim();
