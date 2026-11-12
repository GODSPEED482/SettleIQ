import { Background, Controls, ReactFlow, type Connection } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useMemo, useState } from "react";

import { toFlow, type Layer, type Term } from "./graph";

const LAYERS: { layer: Layer; label: string }[] = [
  { layer: "core", label: "Platform core" },
  { layer: "pack", label: "Domain pack" },
  { layer: "tenant", label: "Tenant extension" },
];

// The graph of the composed ontology. Drawing a line from one class to another starts a new
// relationship with those ends; nothing is saved until the person names it and adds it.
export function Canvas({
  terms,
  onConnect,
}: {
  terms: Term[];
  onConnect?: (ends: { domain: string; range: string }) => void;
}) {
  // Layers switched off are left out of the drawing, with every link that touches them.
  const [hidden, setHidden] = useState<ReadonlySet<Layer>>(new Set());
  const { nodes, edges } = useMemo(() => toFlow(terms, hidden), [terms, hidden]);
  const toggle = (layer: Layer) =>
    setHidden((current) => {
      const next = new Set(current);
      if (!next.delete(layer)) next.add(layer);
      return next;
    });
  return (
    <>
      <ul className="legend" aria-label="Layers">
        {LAYERS.map(({ layer, label }) => (
          <li key={layer} className={`layer-${layer}`}>
            <button type="button" aria-pressed={!hidden.has(layer)} onClick={() => toggle(layer)}>
              {label}
            </button>
          </li>
        ))}
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
