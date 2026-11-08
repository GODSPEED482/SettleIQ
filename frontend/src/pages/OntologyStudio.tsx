import { Background, Controls, ReactFlow } from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { Workspace } from "./Workspace";
import type { WorkspaceDefinition } from "./workspaces";

// Nodes come from GET /api/ontology. Keep the canvas free of finance-specific entity lists.
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
