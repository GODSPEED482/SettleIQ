import type { AuthorityDraft } from "./Authority";
import { bindingKey, type BindingDraft } from "./Bindings";
import type { Authority, DomainPack, Mapping, SourceSummary } from "./types";

// A pack's mapping hints (spec 5.3) for one of the tenant's sources. Hints name a connector, not
// a source, so they apply to every source on that connector. Applying them is an ordinary save
// through the mapping and authority routes, so they are validated and published like any
// binding a person enters.
export type PackBindingSet = {
  namespace: string;
  source: SourceSummary;
  mappings: BindingDraft[];
  authority: AuthorityDraft[];
};

const authorityKey = (row: Pick<Authority, "property_iri" | "entity_type_iri" | "source_id">) =>
  [row.property_iri, row.entity_type_iri, row.source_id].join("|");

// Only what the draft does not already hold, so applying twice adds nothing.
export function packBindingSets(
  packs: DomainPack[],
  sources: SourceSummary[],
  mappings: Mapping[],
  authority: Authority[],
  validFrom: string,
): PackBindingSet[] {
  const bound = new Set(mappings.map(bindingKey));
  const asserted = new Set(authority.map(authorityKey));
  const sets: PackBindingSet[] = [];
  for (const pack of packs) {
    if (pack.status !== "ACTIVE") continue;
    for (const source of sources) {
      const hints = (pack.mapping_hints ?? []).filter((hint) => hint.connector_id === source.connector_id);
      const mappingRows: BindingDraft[] = hints.flatMap((hint) =>
        hint.kind !== "binding"
          ? []
          : hint.fields.map((field) => ({
              source_id: source.id,
              external_object: hint.external_object,
              class_iri: hint.class_iri,
              sync_strategy: hint.sync_strategy,
              method: "accepted_proposal" as const,
              ...field,
            })),
      );
      const authorityRows: AuthorityDraft[] = hints.flatMap((hint) =>
        hint.kind !== "authority"
          ? []
          : [
              {
                property_iri: hint.property_iri,
                entity_type_iri: hint.entity_type_iri,
                source_id: source.id,
                valid_from: validFrom,
                valid_to: null,
                verification_method: hint.verification_method,
              },
            ],
      );
      const set = {
        namespace: pack.namespace,
        source,
        mappings: mappingRows.filter((row) => !bound.has(bindingKey(row))),
        authority: authorityRows.filter((row) => !asserted.has(authorityKey(row))),
      };
      if (set.mappings.length + set.authority.length > 0) sets.push(set);
    }
  }
  return sets;
}
