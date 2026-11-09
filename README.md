# SettleIQ

Multi-tenant, ontology-driven business operations platform. Financial Operations with PayPal Sandbox is the first domain.

- **Normative reference:** [`SettleIQ_Authoritative_Specification_v1.6.md`](SettleIQ_Authoritative_Specification_v1.6.md)
- **Delivery plan:** [`.claude/SettleIQ_3-Day_Delivery_Plan.md`](.claude/SettleIQ_3-Day_Delivery_Plan.md). Task numbers in this repo (for example `1.9`) refer to it.

## What this starter contains

This is the foundation from plan task 1.1, plus what spec §17 says must not be retrofitted. No product feature is built yet. Every route in §12.2 exists and is permission-guarded, but most return `501 NOT_IMPLEMENTED` with the plan task that fills them in.

| Area | Location | State |
|---|---|---|
| Deny-by-default route guard, route map | `backend/app/authz/guard.py` | Working |
| Permission catalogue and sets (seed data) | `backend/app/authz/*.yaml` | Working; metadata is draft |
| Effective-permission resolution, audit of sensitive decisions | `backend/app/authz/access.py` | Working for humans; Agent and Worker arrive in G5 |
| Identity, tenancy, grants, audit schema with forced RLS | `backend/alembic/versions/0001_foundation.py` | Working |
| Transaction-local tenant context | `backend/app/db.py` | Working |
| Connector manifest, capability keys | `backend/app/connectors/` | Draft contract |
| Domain-pack manifest; Finance and SaaS packs | `backend/app/domain_packs/`, `backend/packs/` | Draft contract, mostly empty packs |
| Source-record envelope | `backend/app/sources/envelope.py` | Draft contract |
| Agent tool registry with prohibited-tool check | `backend/app/investigation/tools.py` | Working |
| Decimal-safe money | `backend/packs/finops/money.py` | Working |
| Web shell, permission-driven nav, React Flow canvas | `frontend/src/` | Skeleton |
| **Authentication** | `authenticate` in `guard.py` | **Always returns 401 until task 1.9** |

## Running it

### With Docker

```
cp .env.example .env
docker compose up --build
```

Web: <http://localhost:5173>. API docs: <http://localhost:8000/docs>. The `migrate` service applies migrations and seeds the permission catalogue before the API starts.

### Without Docker

You need Python 3.12 with [uv](https://docs.astral.sh/uv/), Node 22, and a PostgreSQL 16 you can connect to as a superuser. Set `DATABASE_URL`, `MIGRATION_DATABASE_URL` and `APP_DB_PASSWORD` as in `.env.example`.

```
cd backend
uv sync
uv run alembic upgrade head
uv run python -m app.authz.seed
uv run uvicorn app.main:app --reload
```

```
cd frontend
npm ci
npm run dev
```

## Tests

```
cd backend
uv run ruff check .
uv run pytest
```

```
cd frontend
npm test
npm run build
```

Tests marked `db` run as the runtime role against a migrated database. Locally they are skipped if none is reachable. CI sets `REQUIRE_DB_TESTS=1`, which turns an unreachable database into a failure. Check the skip count before trusting a local green run.

## Rules the code enforces

CI or startup fails if any of these break:

- **Every route declares exactly one access rule:** `public()`, `identity()` or `require("<permission.key>")`. An undeclared route stops the app from starting.
- **Authorization checks permission keys, never set names.** Sets are data in `permission_sets.yaml`.
- **Every table with `tenant_id` has forced RLS, a policy and a `NOT NULL` column.** Call `enable_tenant_rls` in the migration that creates it.
- **`app/` never imports from `packs/`.** Finance semantics stay in the pack.
- **The Agent registry contains no request, approve, reject or execute tool.**
- **Connector manifests and packs use only canonical capability keys** from `capability_registry.yaml`.

## Writing a tenant-owned handler

```python
@router.get("/api/things")
def list_things(ctx: TenantContext = require("thing.read")) -> list[dict]:
    with tenant_tx(ctx) as conn:
        ...
```

`tenant_tx` takes only the `TenantContext` built by the guard, so a browser-supplied ID cannot open a tenant transaction. Tenant selection uses the `{tenant_id}` path parameter, or the `X-Tenant-Id` header where the route has none. A non-member and a nonexistent tenant get the same 404.

Routes with no tenant yet (invitation acceptance, webhooks) need a narrowly privileged lookup first, because RLS hides every row until context is set. Webhooks must verify the signature before deriving a tenant (POL-15).

## Draft decisions for the team to ratify

The starter needed a working default for these. Each owner should confirm or change it:

| Decision | Default chosen | Owner | Spec item |
|---|---|---|---|
| Error envelope | `{"error": {"code", "message", ...}}` | Suprava | OPEN-14 |
| Tenant selector on routes without a tenant path | `X-Tenant-Id` header | Suprava | OPEN-14 |
| Connector manifest fields | `app/connectors/manifest.py` | Rahul | OPEN-01, OPEN-CAP-01 |
| Envelope evolution | Spec §7.1 fields only | Rahul | OPEN-04 |
| Domain-pack manifest fields | `app/domain_packs/manifest.py` | Swapnaneel | OPEN-03 |
| Permission sensitivity and narrowable scopes | `permissions.yaml` | Rishav | Spec §3.3 leaves per-key values open |
| Permission caching | None; resolved on every request | Rishav | OPEN-21-19 |
| Auth library and session transport | Not chosen | Swapnaneel, Suprava | OPEN-14, §11.2 |
| LLM provider | Not chosen | Swapnaneel | §6.2 |

## Spec gaps found while scaffolding

- §3.5 gives `platform_security_approver` a "minimum support-request metadata read", but Appendix A has no key for it. The set currently holds only `platform.support.approve`.
- §12.2 omits sign-in, suspend-member, and mapping, authority and entity reads (plan task 1.10). These routes do not exist yet.
- **Unresolved stack conflict:** this starter follows spec §6.2 (FastAPI). `.claude/SettleIQ_Tech_Stack.md` specifies a Node/TypeScript API with Python limited to the agent service. The team needs to pick one before backend work starts. Its choices for auth, LLM and frontend libraries also differ from the "Not chosen" rows above and from the current frontend.
