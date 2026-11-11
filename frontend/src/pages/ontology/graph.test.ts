import { describe, expect, it } from "vitest";

import {
  collectTerms,
  proposalNode,
  relationshipNode,
  tenantTermId,
  toFlow,
  withoutTerm,
  withTerm,
} from "./graph";
import type { OntologyArtifact, TermProposal } from "./types";

const CORE: OntologyArtifact = {
  layer: "CORE",
  namespace: "core",
  version: "1.0.0",
  content_hash: "c",
  content: {
    "@graph": [{ "@id": "core:DomainEntity", "@type": "owl:Class", "rdfs:label": "Domain entity" }],
  },
};
const PACK: OntologyArtifact = {
  layer: "PACK",
  namespace: "pk",
  version: "1.0.0",
  content_hash: "p",
  content: {
    "@graph": [
      { "@id": "pk:Thing", "@type": "owl:Class", "rdfs:label": "Thing", "rdfs:subClassOf": "core:DomainEntity" },
      { "@id": "pk:Part", "@type": "owl:Class", "rdfs:label": "Part", "rdfs:subClassOf": { "@id": "pk:Thing" } },
      { "@id": "pk:size", "@type": "owl:DatatypeProperty", "rdfs:label": "size", "rdfs:domain": "pk:Thing" },
      { "@id": "pk:hasPart", "@type": "owl:ObjectProperty", "rdfs:label": "has part", "rdfs:domain": "pk:Thing", "rdfs:range": "pk:Part" },
    ],
  },
};
const EXTENSION = {
  "@graph": [
    { "@id": "tenant:Widget", "@type": "owl:Class", "rdfs:label": "Widget", "rdfs:subClassOf": "pk:Thing" },
    { "@id": "tenant:madeOf", "@type": "owl:ObjectProperty", "rdfs:label": "made of", "rdfs:domain": "tenant:Widget", "rdfs:range": "pk:Part" },
  ],
};

describe("collectTerms", () => {
  it("labels each term with its layer and kind", () => {
    const terms = collectTerms([CORE, PACK], EXTENSION);
    const byId = Object.fromEntries(terms.map((term) => [term.id, term]));
    expect(byId["core:DomainEntity"]).toMatchObject({ layer: "core", kind: "class" });
    expect(byId["pk:Part"]).toMatchObject({ layer: "pack", subClassOf: ["pk:Thing"] });
    expect(byId["pk:size"].kind).toBe("attribute");
    expect(byId["tenant:madeOf"]).toMatchObject({
      layer: "tenant",
      kind: "relationship",
      domain: "tenant:Widget",
      range: "pk:Part",
    });
  });
});

describe("toFlow", () => {
  const { nodes, edges } = toFlow(collectTerms([CORE, PACK], EXTENSION));

  it("draws classes as nodes, one row per subclass level", () => {
    expect(nodes.map((node) => node.id).sort()).toEqual(
      ["core:DomainEntity", "pk:Part", "pk:Thing", "tenant:Widget"].sort(),
    );
    const y = Object.fromEntries(nodes.map((node) => [node.id, node.position.y]));
    expect(y["core:DomainEntity"]).toBeLessThan(y["pk:Thing"]);
    expect(y["pk:Thing"]).toBeLessThan(y["pk:Part"]);
    expect(nodes.find((node) => node.id === "tenant:Widget")?.className).toContain("layer-tenant");
  });

  it("draws subclass links and relationships as edges", () => {
    const pairs = edges.map((edge) => `${edge.source}>${edge.target}:${edge.label}`);
    expect(pairs).toContain("pk:Part>pk:Thing:subclass of");
    expect(pairs).toContain("pk:Thing>pk:Part:has part");
    expect(pairs).toContain("tenant:Widget>pk:Part:made of");
  });

  it("skips a relationship whose ends are not drawn", () => {
    const { edges: only } = toFlow(collectTerms([PACK], null));
    expect(only.some((edge) => edge.id === "rel:pk:hasPart")).toBe(true);
    const dangling = toFlow(
      collectTerms([], {
        "@graph": [
          { "@id": "tenant:x", "@type": "owl:ObjectProperty", "rdfs:domain": "a:B", "rdfs:range": "a:C" },
        ],
      }),
    );
    expect(dangling.edges).toEqual([]);
  });
});

describe("tenant terms", () => {
  it("forms a tenant id from a label", () => {
    expect(tenantTermId("settled by")).toBe("tenant:settledBy");
    expect(tenantTermId("  Made-of  part ")).toBe("tenant:madeOfPart");
    expect(tenantTermId("2nd link")).toBeNull();
    expect(tenantTermId("   ")).toBeNull();
  });

  it("builds a relationship with one domain and one range", () => {
    expect(relationshipNode({ label: "made of", domain: "tenant:Widget", range: "pk:Part" })).toEqual({
      "@id": "tenant:madeOf",
      "@type": "owl:ObjectProperty",
      "rdfs:label": "made of",
      "rdfs:domain": "tenant:Widget",
      "rdfs:range": "pk:Part",
    });
  });

  it("adds, replaces and removes terms without touching the rest", () => {
    const graph = EXTENSION["@graph"];
    const replaced = withTerm(graph, { "@id": "tenant:madeOf", "rdfs:label": "built from" });
    expect(replaced).toHaveLength(2);
    expect(replaced[1]["rdfs:label"]).toBe("built from");
    expect(withoutTerm(graph, "tenant:madeOf").map((node) => node["@id"])).toEqual(["tenant:Widget"]);
  });

  it("turns an accepted proposal into a term of its kind", () => {
    const proposal: TermProposal = {
      type: "term",
      id: "tenant:Vip",
      kind: "class",
      label: "VIP",
      subclass_of: "pk:Thing",
      domain: null,
      range: null,
      status: "PROPOSED",
      basis: "inferred",
      evidence: [],
    };
    expect(proposalNode(proposal)).toEqual({
      "@id": "tenant:Vip",
      "@type": "owl:Class",
      "rdfs:label": "VIP",
      "rdfs:subClassOf": "pk:Thing",
    });
    expect(proposalNode({ ...proposal, kind: "relationship", domain: "pk:Thing", range: "pk:Part" })).toMatchObject({
      "@type": "owl:ObjectProperty",
      "rdfs:domain": "pk:Thing",
      "rdfs:range": "pk:Part",
    });
    expect(proposalNode({ ...proposal, kind: "property", domain: "pk:Thing" })["@type"]).toBe(
      "owl:DatatypeProperty",
    );
  });
});
