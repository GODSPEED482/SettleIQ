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
| Authentication: sessions, email verification, tenant creation | `backend/app/auth/`, `backend/app/tenancy/creation.py` | Working; sending needs an SMTP relay |

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
| Connector manifest, negotiation and lifecycle | Decided for this build. Fields frozen in `app/connectors/manifest.py`; unsupported capabilities are required booleans, and rate limits and retries are typed, so an unverified provider value cannot ship. Keys are canonical (`capability_registry.yaml`); a legacy spelling loads only through a registered, versioned alias. A source binds one declared protocol and source type and snapshots the capabilities; callers negotiate by key presence, because packs reference keys without versions. A source stays pinned to its registration version: minor and patch are additive, and a different major or an older manifest fails closed. There is no upgrade route: disable and re-register | Rahul | OPEN-01, OPEN-CAP-01 |
| Envelope evolution, metadata, events and files | Decided for this build. The §7.1 fields are frozen; a new field must be nullable and additive with a migration and a spec revision, and there is no envelope version field. Optional ingestion metadata, including provenance (`provider_report` versus `synthetic_reference`), lives on the raw evidence as a typed `RetrievalContext`, not in `attributes`. A record is one observation: a replay collapses, a changed payload is a new record (migration 0009). Event and file conventions are in `app/sources/conventions.py` | Rahul | OPEN-04 |
| Domain-pack manifest and activation | Fields frozen in `app/domain_packs/manifest.py`. Versions are PEP 440 ranges; a conflict declared by either pack or an unmet dependency refuses activation; namespaces are unique and IRIs use the pack namespace; rollback re-pins an earlier published version. Multi-pack overrides stay under OPEN-05 | Swapnaneel | OPEN-03 |
| Permission sensitivity and narrowable scopes | Reviewed per key in `permissions.yaml`. Permission sets stay at version 1 and re-seed in place in this build | Rishav | Spec §3.3 leaves per-key values open |
| Permission caching | Decided for this build: no cache. Every decision re-resolves from current membership and grants, so revocation applies on the next decision, including mid-run for the agent. The spec item stays open for production | Rishav | OPEN-21-19 |
| Stack gaps from task 1.1 | Raw evidence: local volume. Secrets: SSM rendered to env at deploy. Email: SMTP relay. Signing: GitHub Actions | Rishav | Delivery plan |
| Auth and session transport | `argon2-cffi` password hashing with opaque server-side sessions in Postgres, sent as an HttpOnly cookie. Session and token handling is our own code, because packaged libraries use verification JWTs that §11.2 does not allow | Swapnaneel, Suprava | OPEN-14, §11.2 |
| LLM provider | OpenAI, through `agent/` only; the model name comes from `LLM_MODEL` | Swapnaneel | §6.2 |

## Spec gaps found while scaffolding

- §3.5 gives `platform_security_approver` a "minimum support-request metadata read", but Appendix A has no key for it. The set currently holds only `platform.support.approve`.
- §12.2 omits sign-in, suspend-member, and mapping, authority and entity reads (plan task 1.10). These routes do not exist yet.
- **Backend stack settled:** the backend is FastAPI, following spec §6.2 (confirmed 2026-11-09). The Node/TypeScript API in `.claude/SettleIQ_Tech_Stack.md` is superseded. That document's frontend library choices (TanStack Router, shadcn/ui, AG Grid) still differ from the current frontend and remain open.
