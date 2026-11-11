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
| Error envelope | Decided for this build. `{"error": {"code", "message", ...}}` from `app/errors.py`; extra keys sit beside `code` and `message`, and clients read only those two. An inaccessible tenant or resource is always the same 404 | Suprava | OPEN-14 |
| Tenant selector on routes without a tenant path | Decided for this build. The `X-Tenant-Id` header. It only selects a tenant; membership proves access | Suprava | OPEN-14 |
| API versioning | Decided for this build. `/api` is version 1, stated in the OpenAPI `info.version` and in an `API-Version: 1` response header. Version 1 changes are additive only; a breaking change ships under `/api/v2` alongside it | Suprava | OPEN-14, §12.1 |
| Connector manifest, negotiation and lifecycle | Decided for this build. Fields frozen in `app/connectors/manifest.py`; unsupported capabilities are required booleans, and rate limits and retries are typed, so an unverified provider value cannot ship. Keys are canonical (`capability_registry.yaml`); a legacy spelling loads only through a registered, versioned alias. A source binds one declared protocol and source type and snapshots the capabilities; callers negotiate by key presence, because packs reference keys without versions. A source stays pinned to its registration version: minor and patch are additive, and a different major or an older manifest fails closed. There is no upgrade route: disable and re-register. Discovery runs only for a manifest with `supports_discovery`: `merchant-postgres` (the read source §4.4.1 keeps apart from the `internal-merchant` write adapter) and `tenant-csv` | Rahul | OPEN-01, OPEN-CAP-01 |
| Envelope evolution, metadata, events and files | Decided for this build. The §7.1 fields are frozen; a new field must be nullable and additive with a migration and a spec revision, and there is no envelope version field. Optional ingestion metadata, including provenance (`provider_report` versus `synthetic_reference`), lives on the raw evidence as a typed `RetrievalContext`, not in `attributes`. A record is one observation: a replay collapses, a changed payload is a new record (migration 0009). Event and file conventions are in `app/sources/conventions.py` | Rahul | OPEN-04 |
| Domain-pack manifest and activation | Fields frozen in `app/domain_packs/manifest.py`. Versions are PEP 440 ranges; a conflict declared by either pack or an unmet dependency refuses activation; namespaces are unique and IRIs use the pack namespace; rollback re-pins an earlier published version. Multi-pack overrides stay under OPEN-05 | Swapnaneel | OPEN-03 |
| Permission sensitivity and narrowable scopes | Reviewed per key in `permissions.yaml`. Permission sets stay at version 1 and re-seed in place in this build | Rishav | Spec §3.3 leaves per-key values open |
| Permission caching | Decided for this build: no cache. Every decision re-resolves from current membership and grants, so revocation applies on the next decision, including mid-run for the agent. The spec item stays open for production | Rishav | OPEN-21-19 |
| Stack gaps from task 1.1 | Raw evidence: local volume. Secrets: SSM rendered to env at deploy. Email: SMTP relay. Signing: GitHub Actions | Rishav | Delivery plan |
| Source secret references | Decided for this build. A source stores an opaque reference, never a value, set through `PUT /api/sources/{id}/credentials` (`source.credential.manage` only, AC-PP-10), which refuses a reference that does not resolve and audits create and rotate without the reference. `env:SOURCE_SECRET_<NAME>` reads the process environment (local and CI). `managed:<NAME>` reads `<SOURCE_SECRET_DIR>/<NAME>`: `deploy/deploy.sh` renders that read-only directory on the host from SSM `/settleiq/prod/source/<NAME>`, because containers cannot reach the instance role. Add one with `aws ssm put-parameter --type SecureString --name /settleiq/prod/source/<NAME>` and redeploy before registering it. Webhook ID and event types go in `config.webhook` through `PUT /api/sources/{id}/webhooks` (`source.webhook.manage`), limited to the event types the source's pinned `webhooks.receive` contract lists | Rishav | §11.3, task 4.5 |
| Auth and session transport | Decided for this build. `argon2-cffi` password hashing with opaque server-side sessions in Postgres, sent as an HttpOnly cookie that the frontend never reads. Writes from another Origin are refused. Session and token handling is our own code, because packaged libraries use verification JWTs that §11.2 does not allow | Swapnaneel, Suprava | OPEN-14, §11.2 |
| LLM provider | OpenAI, through `agent/` only; the model name comes from `LLM_MODEL` | Swapnaneel | §6.2 |
| Included-in-net semantics for provider reports | Decided for this build. `packs/finops/settlement_treatment.yaml` classifies each entry by PayPal transaction event code: T00 capture, T11 refund, T01 fee, T1201 chargeback, and T12 and T19 adjustment are included; T02, T03, T04 (the withdrawal is the bank-credit side), T13, T15 and T21 are excluded. Amounts are signed as reported. Expected Net is the sum of included entries' nets. A reported net already includes its fee, so that fee is never subtracted again; without a net, gross plus the (negative) fee counts once. An unlisted code, or a sign that contradicts its category, is reported as unclassified rather than guessed. Different currencies are never settled together. `packs/finops/settlement.py` is the one calculation, shared with the `calculate_settlement_variance` agent tool; `test_settlement.py` is the evidence. The 3.8 fixture manifest states each row's treatment from this table | Swapnaneel | OPEN-10, §7.4, §10.3.1 |
| Accepting a generated proposal | Decided for this build. Draft reads return proposals with their observed or inferred basis, and the server records no accept or reject state. A person copies one in through `PATCH /api/ontology/drafts/{id}` and `POST /api/mappings` with `method: accepted_proposal`, so accepted content is covered by the candidate hash | Swapnaneel | §5.7, AC-ONT-03 |

## PayPal provider facts (OPEN-PP-01)

Checked against PayPal's developer documentation on 2026-11-10. `app/connectors/manifests/paypal.yaml` carries the values; nothing below is assumed beyond what these pages say (Appendix C).

| Fact | Source |
|---|---|
| PayPal publishes no rate-limiting policy. Exceeding it returns HTTP 429 `RATE_LIMIT_REACHED`. Do not poll; use webhooks; cache the access token. `Retry-After` and backoff are not documented, so honouring `Retry-After` is our policy | [Rate limiting](https://developer.paypal.com/api/rest/reference/rate-limiting/) |
| Client-credentials token from `/v1/oauth2/token`; the response gives `expires_in` (seconds) and space-separated `scope` URIs | [Authentication](https://developer.paypal.com/api/rest/authentication/) |
| Transaction Search: at most 31 days per request, `page_size` 1 to 500 (default 100), up to 3 hours before a transaction appears. Scope `https://uri.paypal.com/services/reporting/search/read`. Not a real-time stream (spec §10.3) | [List transactions](https://developer.paypal.com/api/transaction-search/v1/search-get) |
| Payments v2: `GET /v2/payments/captures/{capture_id}`, `POST /v2/payments/captures/{capture_id}/refund` (an `amount` makes it partial), `GET /v2/payments/refunds/{refund_id}`. Refund scope `https://uri.paypal.com/services/payments/refund` | [Payments v2](https://developer.paypal.com/api/payments/v2/captures-refund) |
| `PayPal-Request-Id` makes a request idempotent: a repeat returns the latest status of the original. The retention period is not stated, two simultaneous requests with one key may fail the second, and reuse with a different payload is not described. So the Worker reads back before any retry and never reuses a key for a different payload | [Idempotency](https://developer.paypal.com/api/rest/reference/idempotency/) |
| Webhooks: any non-2xx response is redelivered up to 25 times over 3 days, then marked Failed (resend from the dashboard). Verify by CRC32 signature or the verify-signature endpoint | [Webhooks](https://developer.paypal.com/api/rest/webhooks/) |
| SFTP reports need PayPal support to enable report access, and transaction reports need a separate subscription; setup can take 48 hours. Sandbox host is `reports.sandbox.paypal.com`. A Sandbox app does not by itself provide a Balance Report, so plan on the §10.3.1 SYNTHETIC/REFERENCE fixture (task 3.8) | [Access SFTP reports](https://developer.paypal.com/reports/access) |

Still to check with the team's Sandbox app (task 4.1): that the token's scopes include Transaction Search and refunds once Transaction Search is enabled for the app, that the webhook registers, and whether Sandbox report access is enabled.

## Spec gaps found while scaffolding

- §3.5 gives `platform_security_approver` a "minimum support-request metadata read", but Appendix A has no key for it. The set currently holds only `platform.support.approve`.
- §12.2 omits sign-in, suspend-member, and mapping, authority and entity reads; task 1.10 added them. It also omits a connector list: `GET /api/connectors` (`connector.read`, task 4.7) returns each manifest's declared contract for the Sources screen.
- **Backend stack settled:** the backend is FastAPI, following spec §6.2 (confirmed 2026-11-09). The Node/TypeScript API in `.claude/SettleIQ_Tech_Stack.md` is superseded. That document's frontend library choices (TanStack Router, shadcn/ui, AG Grid) still differ from the current frontend and remain open.
