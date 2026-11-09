"""Applies the permission catalogue and system-managed sets. Safe to re-run.

Run after migrations with the migration role: `python -m app.authz.seed`.
Creates catalogue data only; never users, tenants or grants (AC-IAM-11).
"""

import os
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import Connection, create_engine, text

from app.authz.catalogue import get_catalogue, load_permission_sets
from app.domain_packs.manifest import load_domain_packs


def _set_id(key: str, version: int) -> str:
    return str(uuid5(NAMESPACE_URL, f"settleiq:permission-set:{key}:{version}"))


# TODO(G1 exit Rishav): all four teammates join through real invitations; nothing is seeded.
def seed(conn: Connection) -> None:
    catalogue = get_catalogue()
    for spec in catalogue:
        conn.execute(
            text(
                """
                INSERT INTO permissions (key, resource, action, scope_type, narrowable_scopes,
                    principal_types, sensitivity, grant_requires, tenant_grantable, namespace,
                    release, description)
                VALUES (:key, :resource, :action, :scope_type, :narrowable, :principals,
                    :sensitivity, :grant_requires, :grantable, :namespace, :release, :description)
                ON CONFLICT (key) DO UPDATE SET
                    resource = EXCLUDED.resource, action = EXCLUDED.action,
                    scope_type = EXCLUDED.scope_type,
                    narrowable_scopes = EXCLUDED.narrowable_scopes,
                    principal_types = EXCLUDED.principal_types,
                    sensitivity = EXCLUDED.sensitivity,
                    grant_requires = EXCLUDED.grant_requires,
                    tenant_grantable = EXCLUDED.tenant_grantable,
                    namespace = EXCLUDED.namespace, release = EXCLUDED.release,
                    description = EXCLUDED.description
                """
            ),
            {
                "key": spec.key,
                "resource": spec.resource,
                "action": spec.action,
                "scope_type": spec.scope_type,
                "narrowable": list(spec.narrowable_scopes),
                "principals": list(spec.principal_types),
                "sensitivity": spec.sensitivity,
                "grant_requires": list(spec.grant_requires),
                "grantable": spec.tenant_grantable,
                "namespace": spec.namespace,
                "release": spec.release,
                "description": spec.description,
            },
        )

    # TODO(G1 1.3 Rishav): version is fixed at 1 and re-seeding rewrites items in place; decide
    # how a changed set gets a new version (spec 3.3 calls sets versioned).
    version = 1
    for permission_set in load_permission_sets(catalogue):
        set_id = _set_id(permission_set.key, version)
        conn.execute(
            text(
                """
                INSERT INTO permission_sets
                    (id, key, version, display_name, principal_type, status, derivation)
                VALUES (:id, :key, :version, :display_name, :principal_type, 'ACTIVE', :derivation)
                ON CONFLICT (key, version) DO UPDATE SET
                    display_name = EXCLUDED.display_name,
                    principal_type = EXCLUDED.principal_type,
                    derivation = EXCLUDED.derivation
                """
            ),
            {
                "id": set_id,
                "key": permission_set.key,
                "version": version,
                "display_name": permission_set.display_name,
                "principal_type": permission_set.principal_type,
                "derivation": permission_set.derivation,
            },
        )
        conn.execute(
            text("DELETE FROM permission_set_items WHERE permission_set_id = :id"), {"id": set_id}
        )
        items = [(key, "ALLOW") for key in permission_set.allow]
        items += [(key, "DENY") for key in permission_set.deny]
        for key, effect in items:
            conn.execute(
                text(
                    """
                    INSERT INTO permission_set_items (permission_set_id, permission_key, effect)
                    VALUES (:id, :key, :effect)
                    """
                ),
                {"id": set_id, "key": key, "effect": effect},
            )


def seed_domain_packs(conn: Connection) -> None:
    """Record the installed packs. Nothing here activates a pack for any tenant."""
    for pack in load_domain_packs().values():
        conn.execute(
            text(
                """
                INSERT INTO domain_packs (namespace, version, manifest)
                VALUES (:namespace, :version, CAST(:manifest AS jsonb))
                ON CONFLICT (namespace, version) DO UPDATE SET manifest = EXCLUDED.manifest
                """
            ),
            {
                "namespace": pack.namespace,
                "version": pack.version,
                "manifest": pack.model_dump_json(),
            },
        )


def main() -> None:
    engine = create_engine(os.environ["MIGRATION_DATABASE_URL"])
    with engine.begin() as conn:
        seed(conn)
        seed_domain_packs(conn)


if __name__ == "__main__":
    main()
