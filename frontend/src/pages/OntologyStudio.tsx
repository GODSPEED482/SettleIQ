import { Background, Controls, ReactFlow } from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { Workspace } from "./Workspace";
import type { WorkspaceDefinition } from "./workspaces";

// Nodes come from GET /api/ontology. Keep the canvas free of finance-specific entity lists.
// TODO(G2 2.8 Suprava): draw nodes and edges from GET /api/ontology and the open draft; edit
// relationships through PATCH /api/ontology/drafts/{id} with expected_revision, reloading on
// 409 STALE_DRAFT. Show generated proposals apart from accepted terms, labelled observed or
// inferred (spec 5.7), and never accept one without a person's action.
// TODO(G2 2.9 Suprava): binding editor (POST /api/mappings, retire) and authority editor
// (POST /api/authority, retire), showing the mappings.* validator messages next to each row.
// TODO(G2 2.10 Suprava): publish confirmation showing the candidate content hash and the
// validation run with per-validator results. Enable it only for a passed run whose hash matches
// the current candidate, and send confirm_publish=true.
export function OntologyStudio({ workspace }: { workspace: WorkspaceDefinition }) {
  return (
    <>
      <Workspace workspace={workspace} />
      <div className="canvas">
        <ReactFlow nodes={[]} edges={[]} fitView>
          <Background />
          <Controls />
        </ReactFlow>
      </div>
    </>
  );
}
