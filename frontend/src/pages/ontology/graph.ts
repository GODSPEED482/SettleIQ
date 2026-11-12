import type { Edge, Node } from "@xyflow/react";

import type { IriValue, JsonLdNode, OntologyArtifact, TenantExtension, TermProposal } from "./types";

// The composed ontology as the Studio draws it. Classes are nodes; subclass links and
// relationships (owl:ObjectProperty with one domain and one range) are edges. Every term comes
// from the API, so the Studio holds no pack's entity list.

export type Layer = "core" | "pack" | "tenant";
export type TermKind = "class" | "attribute" | "relationship" | "other";

export type Term = {
  id: string;
  label: string;
  layer: Layer;
  kind: TermKind;
  subClassOf: string[];
  domain: string | null;
  range: string | null;
};

const TENANT_TERM = /^tenant:[A-Za-z][A-Za-z0-9_]*$/;

function iri(value: IriValue | undefined): string | null {
  if (value === undefined) return null;
  return typeof value === "string" ? value : value["@id"];
}

function iris(value: IriValue | IriValue[] | undefined): string[] {
  const list = value === undefined ? [] : Array.isArray(value) ? value : [value];
  return list.map((item) => iri(item)).filter((item): item is string => !!item);
}

function kindOf(node: JsonLdNode): TermKind {
  const types = Array.isArray(node["@type"]) ? node["@type"] : [node["@type"]];
  if (types.includes("owl:Class")) return "class";
  if (types.includes("owl:ObjectProperty")) return "relationship";
  if (types.includes("owl:DatatypeProperty")) return "attribute";
  return "other";
}

export function toTerm(node: JsonLdNode, layer: Layer): Term {
  return {
    id: node["@id"],
    label: node["rdfs:label"] ?? node["@id"],
    layer,
    kind: kindOf(node),
    subClassOf: iris(node["rdfs:subClassOf"]),
    domain: iri(node["rdfs:domain"]),
    range: iri(node["rdfs:range"]),
  };
}

export function collectTerms(
  artifacts: OntologyArtifact[],
  extension: TenantExtension | null,
): Term[] {
  const base = artifacts.flatMap((artifact) =>
    artifact.content["@graph"].map((node) =>
      toTerm(node, artifact.layer === "CORE" ? "core" : "pack"),
    ),
  );
  return [...base, ...(extension?.["@graph"] ?? []).map((node) => toTerm(node, "tenant"))];
}

const COLUMN_WIDTH = 200;
const ROW_HEIGHT = 110;

// Each class sits one row below the class it specialises, so the hierarchy reads top-down.
function depthOf(term: Term, byId: Map<string, Term>, seen = new Set<string>()): number {
  const parent = term.subClassOf.map((id) => byId.get(id)).find(Boolean);
  if (!parent || seen.has(term.id)) return 0;
  seen.add(term.id);
  return 1 + depthOf(parent, byId, seen);
}

export function toFlow(
  terms: Term[],
  hidden: ReadonlySet<Layer> = new Set(),
): { nodes: Node[]; edges: Edge[] } {
  const shown = terms.filter((term) => !hidden.has(term.layer));
  const classes = shown.filter((term) => term.kind === "class");
  const byId = new Map(classes.map((term) => [term.id, term]));
  const perRow = new Map<number, number>();
  const nodes: Node[] = classes.map((term) => {
    const depth = depthOf(term, byId);
    const column = perRow.get(depth) ?? 0;
    perRow.set(depth, column + 1);
    return {
      id: term.id,
      position: { x: column * COLUMN_WIDTH, y: depth * ROW_HEIGHT },
      data: { label: term.label },
      className: `onto-node layer-${term.layer}`,
      deletable: false,
    };
  });
  const edges: Edge[] = [];
  for (const term of classes) {
    for (const parent of term.subClassOf) {
      if (!byId.has(parent)) continue;
      edges.push({
        id: `sub:${term.id}:${parent}`,
        source: term.id,
        target: parent,
        label: "subclass of",
        className: "onto-edge subclass",
        deletable: false,
      });
    }
  }
  for (const term of shown) {
    if (term.kind !== "relationship" || !term.domain || !term.range) continue;
    if (!byId.has(term.domain) || !byId.has(term.range)) continue;
    edges.push({
      id: `rel:${term.id}`,
      source: term.domain,
      target: term.range,
      label: term.label,
      className: `onto-edge layer-${term.layer}`,
      deletable: false,
    });
  }
  return { nodes, edges };
}

// "settled by" becomes tenant:settledBy. Returns null when no valid tenant term can be formed.
export function tenantTermId(label: string): string | null {
  const words = label.trim().split(/[^A-Za-z0-9]+/).filter(Boolean);
  if (words.length === 0) return null;
  const [first, ...rest] = words;
  const name =
    first.charAt(0).toLowerCase() +
    first.slice(1) +
    rest.map((word) => word.charAt(0).toUpperCase() + word.slice(1)).join("");
  const id = `tenant:${name}`;
  return TENANT_TERM.test(id) ? id : null;
}

export type RelationshipInput = { label: string; domain: string; range: string };

export function relationshipNode(input: RelationshipInput): JsonLdNode | null {
  const id = tenantTermId(input.label);
  if (!id) return null;
  return {
    "@id": id,
    "@type": "owl:ObjectProperty",
    "rdfs:label": input.label.trim(),
    "rdfs:domain": input.domain,
    "rdfs:range": input.range,
  };
}

// A new tenant term replaces any earlier term with the same id, so an edit is one PATCH.
export function withTerm(graph: JsonLdNode[], node: JsonLdNode): JsonLdNode[] {
  return [...graph.filter((item) => item["@id"] !== node["@id"]), node];
}

export function withoutTerm(graph: JsonLdNode[], id: string): JsonLdNode[] {
  return graph.filter((item) => item["@id"] !== id);
}

// The JSON-LD a person adds when accepting a generated term. Nothing is added without that act.
export function proposalNode(proposal: TermProposal): JsonLdNode {
  const node: JsonLdNode = { "@id": proposal.id, "rdfs:label": proposal.label };
  if (proposal.kind === "class") {
    node["@type"] = "owl:Class";
    if (proposal.subclass_of) node["rdfs:subClassOf"] = proposal.subclass_of;
  } else if (proposal.kind === "relationship") {
    node["@type"] = "owl:ObjectProperty";
    if (proposal.domain) node["rdfs:domain"] = proposal.domain;
    if (proposal.range) node["rdfs:range"] = proposal.range;
  } else {
    node["@type"] = "owl:DatatypeProperty";
    if (proposal.domain) node["rdfs:domain"] = proposal.domain;
  }
  return node;
}
