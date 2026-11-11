// Shapes returned by the source routes in backend/app/api/sources.py. A source never carries
// its secret reference, only whether one is stored (spec 11.3).

import type { Provenance } from "../operations/types";

export type DeclaredCapability = {
  key: string;
  version: string;
  parameters: Record<string, unknown>;
};

export type SupportFlag =
  | "supports_discovery"
  | "supports_metadata"
  | "supports_batch_extraction"
  | "supports_incremental"
  | "supports_events"
  | "supports_file_ingestion"
  | "supports_detail_lookup";

// Manifests state every unsupported capability, so the screen shows both sides.
export const SUPPORT_FLAGS: [SupportFlag, string][] = [
  ["supports_discovery", "Discovery"],
  ["supports_metadata", "Metadata"],
  ["supports_batch_extraction", "Batch extraction"],
  ["supports_incremental", "Incremental"],
  ["supports_events", "Events"],
  ["supports_file_ingestion", "File ingestion"],
  ["supports_detail_lookup", "Detail lookup"],
];

export type ConnectorManifest = {
  connector_id: string;
  version: string;
  protocols: string[];
  source_types: string[];
  auth_mode: string;
  secret_reference_required: boolean;
  extraction_modes: string[];
  capabilities: DeclaredCapability[];
} & Record<SupportFlag, boolean>;

export type WebhookConfig = { webhook_id: string; event_types: string[] };

export type Source = {
  id: string;
  connector_id: string;
  connector_version: string;
  source_type: string;
  protocol: string;
  display_name: string;
  config: Record<string, unknown> & { webhook?: WebhookConfig };
  capabilities: DeclaredCapability[];
  status: string;
  has_secret_reference: boolean;
  created_at: string;
  updated_at: string;
};

export type CreateSourceInput = {
  connector_id: string;
  protocol: string;
  source_type: string;
  display_name: string;
  config: Record<string, unknown>;
};

export type DiscoveredObject = {
  object_name: string;
  object_type: string;
  discovered_at: string;
  row_count: number | null;
  columns: number;
};

export type DiscoverResult = { source_id: string; objects: DiscoveredObject[] };

export type SyncResult = { source_id: string; record_count: number };

export type FileKind = "BANK_CSV" | "BRR";

export type UploadResult = {
  source_id: string;
  evidence_id: string;
  object_name: string;
  checksum: string;
  provenance: Provenance;
  record_count: number;
};
