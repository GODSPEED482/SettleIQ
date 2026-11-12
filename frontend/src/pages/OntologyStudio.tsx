import { useEffect, useMemo, useState } from "react";

import { ApiError, messageOf } from "../api/client";
import { can } from "../permissions/can";
import { usePermissions } from "../permissions/PermissionsContext";
import { ontologyApi } from "./ontology/api";
import { Authority, type AuthorityDraft } from "./ontology/Authority";
import { bindingFromProposal, bindingKey, Bindings, type BindingDraft } from "./ontology/Bindings";
import { Canvas } from "./ontology/Canvas";
import { DomainPacks } from "./ontology/DomainPacks";
import { PackBindings } from "./ontology/PackBindings";
import { packBindingSets, packGaps, type PackBindingSet } from "./ontology/packHints";
import {
  collectTerms,
  proposalNode,
  relationshipNode,
  withoutTerm,
  withTerm,
  type RelationshipInput,
} from "./ontology/graph";
import { Proposals } from "./ontology/Proposals";
import { Publish } from "./ontology/Publish";
import { Relationships } from "./ontology/Relationships";
import type {
  Authority as AuthorityRow,
  BindingProposal,
  DomainPack,
  Draft,
  GenerationJob,
  JsonLdNode,
  Mapping,
  PublishedOntology,
  SourceSummary,
  TermProposal,
  ValidationRun,
} from "./ontology/types";
import { Validation } from "./ontology/Validation";
import type { WorkspaceDefinition } from "./workspaces";

const POLL_MS = 2000;

// Ontology Studio (spec 13.1). The published ontology is read-only; changes go to the one open
// draft, each PATCH carrying the revision it was made against, so a stale edit is refused rather
// than overwriting someone else's. Each control shows only with its own key; the API rechecks.
export function OntologyStudio({ workspace }: { workspace: WorkspaceDefinition }) {
  const { tenantId, permissions } = usePermissions();
  const api = useMemo(() => ontologyApi(tenantId ?? ""), [tenantId]);
  const canEdit = can(permissions, "ontology.edit");
  const canGenerate = can(permissions, "ontology.generate");
  const canValidate = can(permissions, "ontology.validate");
  const canPublish = can(permissions, "ontology.publish");
  const canReadMappings = can(permissions, "mapping.read");
  const canEditMappings = can(permissions, "mapping.edit");
  const canReadAuthority = can(permissions, "authority.read");
  const canEditAuthority = can(permissions, "authority.edit");
  const canReadSources = can(permissions, "source.read");
  const canReadPacks = can(permissions, "domain_pack.read");
  const canManagePacks = can(permissions, "domain_pack.manage");

  const [published, setPublished] = useState<PublishedOntology | null>(null);
  const [packs, setPacks] = useState<DomainPack[]>([]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [job, setJob] = useState<GenerationJob | null>(null);
  const [mappings, setMappings] = useState<Mapping[]>([]);
  const [authority, setAuthority] = useState<AuthorityRow[]>([]);
  const [sources, setSources] = useState<SourceSummary[]>([]);
  const [validationRun, setValidationRun] = useState<ValidationRun | null>(null);
  const [bindingPrefill, setBindingPrefill] = useState<BindingDraft | null>(null);
  const [prefill, setPrefill] = useState<{ domain: string; range: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    if (!tenantId) return;
    let current = true;
    const load = async () => {
      const [ontology, open, packRows] = await Promise.all([
        api.published(),
        api.openDraft(),
        canReadPacks ? api.packs() : [],
      ]);
      const [mappingRows, authorityRows, sourceRows] = await Promise.all([
        open && canReadMappings ? api.mappings(open.id) : [],
        open && canReadAuthority ? api.authority(open.id) : [],
        open && canReadSources ? api.sources() : [],
      ]);
      if (!current) return;
      setPublished(ontology);
      setPacks(packRows);
      setDraft(open);
      setMappings(mappingRows);
      setAuthority(authorityRows);
      setSources(sourceRows);
    };
    load().catch((caught) => current && setError(messageOf(caught)));
    return () => {
      current = false;
    };
  }, [tenantId, api, version, canReadPacks, canReadMappings, canReadAuthority, canReadSources]);

  // Follows a generation job until the dispatcher finishes it, then reloads the draft.
  useEffect(() => {
    if (!job || job.status === "SUCCEEDED" || job.status === "FAILED") return;
    const timer = setTimeout(() => {
      api
        .generationJob(job.job_id)
        .then((next) => {
          setJob(next);
          if (next.status === "SUCCEEDED") {
            setNotice("Proposals are ready to review.");
            setVersion((value) => value + 1);
          } else if (next.status === "FAILED") {
            setError("Generation failed. The draft is unchanged.");
          }
        })
        .catch((caught) => setError(messageOf(caught)));
    }, POLL_MS);
    return () => clearTimeout(timer);
  }, [job, api]);

  // The draft when one is open, otherwise the published ontology. Kept stable between renders,
  // so the graph keeps its layout until the content changes.
  const terms = useMemo(
    () =>
      draft
        ? collectTerms(draft.artifacts, draft.tenant_extension)
        : collectTerms(published?.artifacts ?? [], published?.tenant_extension ?? null),
    [draft, published],
  );

  if (!tenantId) return null;

  // Runs one change, reports it, then re-reads the ontology and the draft.
  async function run<T>(work: () => Promise<T>, done: string | ((result: T) => string)) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await work();
      setNotice(typeof done === "function" ? done(result) : done);
      return true;
    } catch (caught) {
      setError(
        caught instanceof ApiError && caught.code === "STALE_DRAFT"
          ? "The draft changed since you loaded it. It has been reloaded; check it and try again."
          : messageOf(caught),
      );
      return false;
    } finally {
      setBusy(false);
      setVersion((value) => value + 1);
    }
  }

  const tenantGraph: JsonLdNode[] = draft?.tenant_extension["@graph"] ?? [];
  const editable = canEdit && !!draft;

  const editGraph = (graph: JsonLdNode[], done: string) =>
    draft ? run(() => api.editDraft(draft, graph), done) : Promise.resolve(false);

  function addRelationship(input: RelationshipInput) {
    const node = relationshipNode(input);
    if (!node) return Promise.resolve(false);
    return editGraph(withTerm(tenantGraph, node), `${input.label.trim()} added to the draft.`);
  }

  const termProposals = (draft?.proposals ?? []).filter(
    (proposal): proposal is TermProposal => proposal.type === "term",
  );
  const bindingProposals = (draft?.proposals ?? []).filter(
    (proposal): proposal is BindingProposal => proposal.type === "binding",
  );
  const inExtension = new Set(tenantGraph.map((node) => node["@id"]));
  const bound = new Set(mappings.map(bindingKey));
  const classes = terms.filter((term) => term.kind === "class");
  const properties = terms.filter((term) => term.kind === "attribute" || term.kind === "relationship");

  const saveBinding = (binding: BindingDraft) =>
    draft
      ? run(() => api.saveMapping({ ...binding, draft_id: draft.id }), "Binding saved.")
      : Promise.resolve(false);
  const knownTerms = new Set(terms.map((term) => term.id));
  // A proposal is ready to accept as it stands when it carries a complete mapping and names
  // terms the draft holds; one leaning on an unaccepted proposed term waits for that term.
  const ready = (proposal: BindingProposal) =>
    !!proposal.mapping &&
    knownTerms.has(proposal.property) &&
    knownTerms.has(proposal.mapping.class_iri) &&
    !bound.has(bindingKey(bindingFromProposal(proposal)));
  const readyBindings = bindingProposals.filter(ready);

  async function saveAll(bindings: BindingDraft[], assertions: AuthorityDraft[]) {
    if (!draft) return;
    for (const binding of bindings) await api.saveMapping({ ...binding, draft_id: draft.id });
    for (const assertion of assertions) await api.saveAuthority({ ...assertion, draft_id: draft.id });
  }

  const packSets = draft
    ? packBindingSets(packs, sources, mappings, authority, new Date().toISOString())
    : [];
  const applyPackBindings = (set: PackBindingSet) =>
    void run(
      () =>
        saveAll(canEditMappings ? set.mappings : [], canEditAuthority ? set.authority : []),
      `${set.namespace} bindings applied to ${set.source.display_name}.`,
    );

  const saveAuthority = (assertion: AuthorityDraft) =>
    draft
      ? run(() => api.saveAuthority({ ...assertion, draft_id: draft.id }), "Authority assertion saved.")
      : Promise.resolve(false);

  return (
    <section className="studio">
      <h1>{workspace.title}</h1>
      <p className="muted">{workspace.summary}</p>
      {error && <p className="error" role="alert">{error}</p>}
      {notice && <p className="notice" role="status">{notice}</p>}

      {canReadPacks && (
        <DomainPacks
          packs={packs}
          draftPacks={
            draft ? draft.artifacts.filter((item) => item.layer === "PACK").map((item) => item.namespace) : null
          }
          canManage={canManagePacks}
          busy={busy}
          onActivate={(namespace) =>
            void run(() => api.managePack(namespace, "ACTIVATE"), `${namespace} activated.`)
          }
          onDisable={(namespace) =>
            void run(() => api.managePack(namespace, "DISABLE"), `${namespace} disabled.`)
          }
        />
      )}

      <section className="panel">
        <h2>Versions</h2>
        <p>
          {published?.version
            ? <>Published version {published.version}, <code>{published.content_hash}</code></>
            : "Nothing has been published yet."}
        </p>
        {draft ? (
          <p>
            Open draft at revision {draft.revision}
            {draft.base_version ? `, based on version ${draft.base_version}` : ""}.
          </p>
        ) : (
          <p className="muted">No draft is open. The graph shows the published ontology.</p>
        )}
        <div className="actions">
          {canEdit && !draft && (
            <button type="button" disabled={busy} onClick={() => void run(api.createDraft, "Draft started.")}>
              Start a draft
            </button>
          )}
          {canGenerate && draft && (
            <button
              type="button"
              className="secondary"
              disabled={busy || job?.status === "QUEUED" || job?.status === "RUNNING"}
              onClick={() =>
                void run(async () => setJob(await api.generate()), "Generating proposals from discovered metadata.")
              }
            >
              Generate proposals
            </button>
          )}
          {canEdit && draft && (
            <button
              type="button"
              className="secondary"
              disabled={busy}
              onClick={() => void run(() => api.abandonDraft(draft.id), "Draft abandoned.")}
            >
              Abandon draft
            </button>
          )}
        </div>
        {job && (job.status === "QUEUED" || job.status === "RUNNING") && (
          <p className="muted" role="status">Generation {job.status.toLowerCase()}…</p>
        )}
      </section>

      <Canvas terms={terms} onConnect={editable ? setPrefill : undefined} />

      <Relationships
        terms={terms}
        editable={editable}
        busy={busy}
        prefill={prefill}
        onAdd={addRelationship}
        onRemove={(id) => void editGraph(withoutTerm(tenantGraph, id), "Relationship removed.")}
      />

      {draft && (
        <Proposals
          terms={termProposals}
          bindings={bindingProposals}
          accepted={(id) => inExtension.has(id)}
          canAccept={editable}
          busy={busy}
          bindingToolbar={
            canEditMappings &&
            readyBindings.length > 0 && (
              <div className="actions">
                <button
                  type="button"
                  disabled={busy}
                  onClick={() =>
                    void run(
                      () => saveAll(readyBindings.map(bindingFromProposal), []),
                      `${readyBindings.length} proposed bindings accepted.`,
                    )
                  }
                >
                  Accept all {readyBindings.length} ready bindings
                </button>
              </div>
            )
          }
          bindingAction={(proposal) => {
            const key = bindingKey({
              source_id: proposal.source_id,
              external_object: proposal.object,
              field_path: proposal.field,
              property_iri: proposal.property,
            });
            if (bound.has(key)) return <span className="badge">Bound</span>;
            if (!canEditMappings) return null;
            return (
              <div className="actions">
                {ready(proposal) && (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void saveBinding(bindingFromProposal(proposal))}
                  >
                    Accept binding
                  </button>
                )}
                <button
                  type="button"
                  className="secondary"
                  disabled={busy}
                  onClick={() => setBindingPrefill(bindingFromProposal(proposal))}
                >
                  {ready(proposal) ? "Edit first" : "Use as binding"}
                </button>
              </div>
            );
          }}
          onAccept={(proposal) =>
            void editGraph(
              withTerm(tenantGraph, proposalNode(proposal)),
              `${proposal.label} accepted into the draft.`,
            )
          }
        />
      )}

      {draft && canReadMappings && (
        <Bindings
          mappings={mappings}
          sources={sources}
          classes={classes}
          properties={properties}
          run={validationRun}
          editable={canEditMappings}
          busy={busy}
          prefill={bindingPrefill}
          onSave={saveBinding}
          onRetire={(mapping) =>
            void run(() => api.retireMapping(mapping.id), `${mapping.field_path} binding retired.`)
          }
        />
      )}

      {draft && canReadMappings && (
        <PackBindings
          sets={packSets}
          gaps={packGaps(packs, mappings, authority)}
          canEditMappings={canEditMappings}
          canEditAuthority={canEditAuthority}
          busy={busy}
          onApply={applyPackBindings}
        />
      )}

      {draft && canReadAuthority && (
        <Authority
          rows={authority}
          sources={sources}
          classes={classes}
          properties={properties}
          run={validationRun}
          editable={canEditAuthority}
          busy={busy}
          onSave={saveAuthority}
          onRetire={(row) => void run(() => api.retireAuthority(row.id), "Authority assertion retired.")}
        />
      )}

      {draft && (
        <Validation
          draft={draft}
          run={validationRun}
          canValidate={canValidate}
          busy={busy}
          onValidate={() =>
            void run(async () => setValidationRun(await api.validate(draft.id)), "Validation finished.")
          }
        />
      )}

      {draft && canPublish && (
        <Publish
          draft={draft}
          run={validationRun}
          nextVersion={(published?.version ?? 0) + 1}
          busy={busy}
          onPublish={(passed) =>
            void run(
              async () => {
                const created = await api.publish(draft.id, passed.validation_run_id);
                setValidationRun(null);
                return created;
              },
              (created) => `Version ${created.version} published.`,
            )
          }
        />
      )}
    </section>
  );
}
