import { Background, Controls, ReactFlow, type Connection } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useMemo } from "react";

import { toFlow, type Term } from "./graph";

// The graph of the composed ontology. Drawing a line from one class to another starts a new
// relationship with those ends; nothing is saved until the person names it and adds it.
export function Canvas({
  terms,
  onConnect,
}: {
  terms: Term[];
  onConnect?: (ends: { domain: string; range: string }) => void;
}) {
  const { nodes, edges } = useMemo(() => toFlow(terms), [terms]);
  return (
    <>
      <ul className="legend" aria-label="Layers">
        <li className="layer-core">Platform core</li>
        <li className="layer-pack">Domain pack</li>
        <li className="layer-tenant">Tenant extension</li>
      </ul>
      <div className="canvas">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodesConnectable={!!onConnect}
          onConnect={(connection: Connection) =>
            onConnect?.({ domain: connection.source, range: connection.target })
          }
          fitView
        >
          <Background />
          <Controls />
        </ReactFlow>
      </div>
    </>
  );
}
