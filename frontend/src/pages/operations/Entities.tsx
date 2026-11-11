import { useEffect, useState } from "react";

import { messageOf } from "../../api/client";
import type { OperationsApi } from "./api";
import { ProvenanceLabel, StatusBadge, shortId } from "./labels";
import type { EntityDetail, EntitySummary, Observation, ResolvedView } from "./types";

type Props = { api: OperationsApi; version: number };

// Canonical entities with every observation and the resolved value per property (spec 5.5, 5.6).
export function Entities({ api, version }: Props) {
  const [entities, setEntities] = useState<EntitySummary[]>([]);
  const [classIri, setClassIri] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<EntityDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    api
      .entities(classIri || undefined)
      .then((list) => current && setEntities(list))
      .catch((caught) => current && setError(messageOf(caught)));
    return () => {
      current = false;
    };
  }, [api, version, classIri]);

  useEffect(() => {
    if (!selected) {
      setDetail(null);
      return;
    }
    let current = true;
    api
      .entity(selected)
      .then((entity) => current && setDetail(entity))
      .catch((caught) => current && setError(messageOf(caught)));
    return () => {
      current = false;
    };
  }, [api, version, selected]);

  const classes = [...new Set([...entities.map((entity) => entity.class_iri), classIri])]
    .filter(Boolean)
    .sort();

  return (
    <section className="panel">
      <h2>Entities</h2>
      {error && <p className="error" role="alert">{error}</p>}
      <div className="form inline filters">
        <label>
          Class
          <select value={classIri} onChange={(event) => setClassIri(event.target.value)}>
            <option value="">Any</option>
            {classes.map((value) => (
              <option key={value}>{value}</option>
            ))}
          </select>
        </label>
      </div>
      {entities.length === 0 ? (
        <p className="muted">No entities yet.</p>
      ) : (
        <ul className="chips entity-list">
          {entities.map((entity) => (
            <li key={entity.id}>
              <button
                type="button"
                className={entity.id === selected ? "chip selected" : "chip"}
                aria-pressed={entity.id === selected}
                onClick={() => setSelected(entity.id === selected ? null : entity.id)}
              >
                {entity.class_iri} {entity.canonical_key}
              </button>
            </li>
          ))}
        </ul>
      )}
      {detail && <EntityView entity={detail} />}
    </section>
  );
}

function EntityView({ entity }: { entity: EntityDetail }) {
  return (
    <div className="entity-detail">
      <h3>
        {entity.class_iri} {entity.canonical_key}
      </h3>
      <h4>Resolved values</h4>
      {entity.resolved.length === 0 ? (
        <p className="muted">No successful run has resolved this entity yet.</p>
      ) : (
        <div className="table-wrap">
          <table aria-label="Resolved values">
            <thead>
              <tr>
                <th>Property</th>
                <th>Status</th>
                <th>Value</th>
                <th>Observations</th>
              </tr>
            </thead>
            <tbody>
              {entity.resolved.map((view) => (
                <tr key={view.property_iri}>
                  <td>{view.property_iri}</td>
                  <td>
                    <StatusBadge status={view.status} />
                  </td>
                  <td>
                    <ResolvedValue view={view} />
                  </td>
                  <td>{view.contributing_observation_ids.map(shortId).join(", ") || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <h4>Observations</h4>
      {entity.observations.length === 0 ? (
        <p className="muted">No observations.</p>
      ) : (
        <div className="table-wrap">
          <table aria-label="Observations">
            <thead>
              <tr>
                <th>Property</th>
                <th>Source</th>
                <th>Field</th>
                <th>Raw</th>
                <th>Normalized</th>
                <th>Basis</th>
                <th>Evidence</th>
              </tr>
            </thead>
            <tbody>
              {entity.observations.map((observation) => (
                <ObservationRow key={observation.id} observation={observation} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// Only RESOLVED has a value. Every other status is shown as it is, never as a chosen value.
function ResolvedValue({ view }: { view: ResolvedView }) {
  if (view.status !== "RESOLVED") return <span className="muted">No value: {view.status}</span>;
  return <>{`${view.value ?? ""} ${view.currency ?? ""}`.trim()}</>;
}

function ObservationRow({ observation }: { observation: Observation }) {
  return (
    <tr>
      <td title={shortId(observation.id)}>{observation.property_iri}</td>
      <td title={observation.source_id}>{shortId(observation.source_id)}</td>
      <td>
        <code>{observation.field_path}</code>
      </td>
      <td>{observation.raw_value ?? "—"}</td>
      <td>
        {observation.normalized_value ?? "—"}
        {observation.currency ? ` ${observation.currency}` : ""}
      </td>
      <td>{observation.value_basis}</td>
      <td>
        <ProvenanceLabel provenance={observation.provenance} />
      </td>
    </tr>
  );
}
