import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { PermissionsProvider } from "../../permissions/PermissionsContext";
import { RequirePermission } from "../../permissions/RequirePermission";
import { SessionProvider } from "../../session/SessionContext";
import { mockApi, type Reply } from "../../test/api";
import { OntologyStudio } from "../OntologyStudio";
import { WORKSPACES } from "../workspaces";
import type { Draft, PublishedOntology } from "./types";

// Shared by the Studio screen tests: one open draft with core, pack and tenant terms, and one
// generated proposal of each type.
export const T = "a0000000-0000-4000-8000-000000000000";
export const STUDIO = WORKSPACES.find((workspace) => workspace.path === "/ontology")!;

export const DRAFT: Draft = {
  id: "d-1",
  revision: 3,
  base_version: null,
  status: "OPEN",
  content_hash: "a".repeat(64),
  artifacts: [
    {
      layer: "CORE",
      namespace: "core",
      version: "1.0.0",
      content_hash: "c",
      content: {
        "@graph": [{ "@id": "core:DomainEntity", "@type": "owl:Class", "rdfs:label": "Domain entity" }],
      },
    },
    {
      layer: "PACK",
      namespace: "pk",
      version: "1.0.0",
      content_hash: "p",
      content: {
        "@graph": [
          { "@id": "pk:Thing", "@type": "owl:Class", "rdfs:label": "Thing", "rdfs:subClassOf": "core:DomainEntity" },
          { "@id": "pk:Part", "@type": "owl:Class", "rdfs:label": "Part", "rdfs:subClassOf": "pk:Thing" },
          { "@id": "pk:hasPart", "@type": "owl:ObjectProperty", "rdfs:label": "has part", "rdfs:domain": "pk:Thing", "rdfs:range": "pk:Part" },
        ],
      },
    },
  ],
  tenant_extension: {
    "@graph": [
      { "@id": "tenant:Widget", "@type": "owl:Class", "rdfs:label": "Widget", "rdfs:subClassOf": "pk:Thing" },
      { "@id": "tenant:madeOf", "@type": "owl:ObjectProperty", "rdfs:label": "made of", "rdfs:domain": "tenant:Widget", "rdfs:range": "pk:Part" },
    ],
  },
  proposals: [
    {
      type: "term",
      id: "tenant:Gadget",
      kind: "class",
      label: "Gadget",
      subclass_of: "pk:Thing",
      domain: null,
      range: null,
      status: "PROPOSED",
      basis: "inferred",
      evidence: [
        {
          kind: "field_profile",
          field: "kind",
          detail: "kind is a field in the discovered metadata.",
          rationale: "values look like gadget types",
          basis: "observed",
        },
        { kind: "name_similarity", field: "gadget_id", detail: "named like a gadget", basis: "inferred" },
      ],
    },
    {
      type: "binding",
      source_id: "s-1",
      object: "items",
      field: "size",
      property: "pk:size",
      confidence: "medium",
      status: "PROPOSED",
      basis: "inferred",
      evidence: [{ kind: "name_similarity", field: "size", detail: "same name", basis: "inferred" }],
    },
  ],
};

export const NOTHING_PUBLISHED: PublishedOntology = { version: null, artifacts: [], tenant_extension: null };

export const granted = (keys: string[]): Reply => ({
  status: 200,
  body: {
    permissions: keys.map((key) => ({ key, tenant_wide: true, scopes: [], denied_scopes: [] })),
  },
});

export function renderStudio(keys: string[], extra: Record<string, Reply | Reply[]> = {}) {
  const api = mockApi({
    "GET /api/me/tenants": { status: 200, body: { tenants: [{ id: T, name: "Acme" }] } },
    [`GET /api/me/permissions?tenant_id=${T}`]: granted(["ontology.read", ...keys]),
    "GET /api/ontology": { status: 200, body: NOTHING_PUBLISHED },
    "GET /api/ontology/drafts/open": { status: 200, body: DRAFT },
    ...extra,
  });
  render(
    <MemoryRouter>
      <SessionProvider>
        <PermissionsProvider>
          <RequirePermission permission="ontology.read">
            <OntologyStudio workspace={STUDIO} />
          </RequirePermission>
        </PermissionsProvider>
      </SessionProvider>
    </MemoryRouter>,
  );
  return api;
}
