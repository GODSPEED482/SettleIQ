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

export type PackGap = { namespace: string; message: string };

// What an active pack's run will skip given the draft's bindings and authority: a bound class
// the pack does not resolve, one nothing identifies, a property its shapes require, a link with
// its shared value unbound on one side, and a bound value no single source is the authority for
// (spec 5.6), which stays unresolved. Each still publishes; the run just finds nothing there.
export function packGaps(
  packs: DomainPack[],
  mappings: Mapping[],
  authority: Authority[] = [],
  now: Date = new Date(),
): PackGap[] {
  const has = (cls: string, property: string) =>
    mappings.some((row) => row.class_iri === cls && row.property_iri === property);
  const gaps: PackGap[] = [];
  for (const pack of packs) {
    if (pack.status !== "ACTIVE" || !pack.identities) continue;
    const add = (message: string) => gaps.push({ namespace: pack.namespace, message });
    const identities = pack.identities;
    const bound = [
      ...new Set(mappings.map((row) => row.class_iri).filter((cls) => cls.startsWith(`${pack.namespace}:`))),
    ].sort();
    if (bound.length === 0) add(`Nothing is bound to a ${pack.namespace} class, so its run has nothing to check.`);
    for (const cls of bound) {
      const keys = identities[cls];
      if (!keys) {
        add(
          `${cls} is not a class ${pack.namespace} resolves, so these bindings create no records. ` +
            `It resolves ${Object.keys(identities).join(", ")}.`,
        );
      } else if (!keys.some((property) => has(cls, property))) {
        add(`Nothing identifies a ${cls}: bind ${keys.join(" or ")} on it.`);
      }
    }
    for (const row of pack.required_properties ?? []) {
      if (bound.includes(row.class_iri) && !has(row.class_iri, row.property_iri)) {
        add(`${row.message} Bind ${row.property_iri} on ${row.class_iri}.`.trim());
      }
    }
    for (const rule of pack.relationships ?? []) {
      if (!bound.includes(rule.subject) || !bound.includes(rule.object)) continue;
      if (!has(rule.subject, rule.via) || !has(rule.object, rule.via)) {
        add(`${rule.subject} links to ${rule.object} only through ${rule.via}, bound on both.`);
      }
    }
    // Identifying and linking values match records; they are never read as resolved values.
    const matching = new Set([
      ...Object.values(identities).flat(),
      ...(pack.relationships ?? []).map((rule) => rule.via),
    ]);
    const read = new Map<string, Set<string>>();
    for (const row of mappings) {
      if (!identities[row.class_iri] || !row.property_iri.startsWith(`${pack.namespace}:`)) continue;
      if (matching.has(row.property_iri)) continue;
      const sources = read.get(`${row.class_iri}|${row.property_iri}`) ?? new Set<string>();
      read.set(`${row.class_iri}|${row.property_iri}`, sources.add(row.source_id));
    }
    const unasserted = new Map<string, string[]>();
    for (const [key, sources] of [...read].sort(([a], [b]) => a.localeCompare(b))) {
      const [cls, property] = key.split("|");
      const current = authority.filter(
        (row) =>
          row.entity_type_iri === cls &&
          row.property_iri === property &&
          new Date(row.valid_from) <= now &&
          (row.valid_to === null || now < new Date(row.valid_to)),
      );
      if (current.length === 0) {
        unasserted.set(cls, [...(unasserted.get(cls) ?? []), property]);
      } else if (current.length > 1) {
        add(`More than one source is the authority for ${property} on ${cls}, so its value is in conflict.`);
      } else if (!sources.has(current[0].source_id)) {
        add(`The authority for ${property} on ${cls} is a source with no binding for it, so it stays unresolved.`);
      } else if (current[0].valid_to) {
        add(
          `The authority for ${property} on ${cls} ends ${current[0].valid_to.slice(0, 10)}; ` +
            "after that its value is unresolved.",
        );
      }
    }
    for (const [cls, properties] of unasserted) {
      add(
        `No source is the authority for ${properties.join(", ")} on ${cls}, so ` +
          `${properties.length > 1 ? "these values stay" : "its value stays"} unresolved and the run cannot read ` +
          `${properties.length > 1 ? "them" : "it"}.`,
      );
    }
  }
  return gaps;
}
