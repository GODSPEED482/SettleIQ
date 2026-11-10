# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

SettleIQ is a multi-tenant, ontology-driven business operations platform; Financial Operations with PayPal Sandbox is the first domain.

## Source documents

- `SettleIQ_Authoritative_Specification_v1.6.md` is the sole normative reference. It is 1,591 lines, so grep for the ID you need (`POL-09`, `AC-MERCH-05`, `§9.3`). Requirement changes need a new integrated revision (§22.2), not a sidecar note.
- `.claude/SettleIQ_3-Day_Delivery_Plan.md` is the delivery plan. It scopes a 3-day vertical slice, not the MVP. Task numbers such as `1.9` in code and stubs refer to it.
- `.claude/SettleIQ_Tech_Stack.md` is the earlier stack and tooling proposal. Its Node/TypeScript backend is superseded; see below.

## Backend stack: FastAPI

- The backend is FastAPI, following spec §6.2: Python owns the API, authorization, migrations and domain packs. Swapnaneel confirmed this on 2026-11-09. Do not propose Node, Express, Kysely, pg-boss, Better Auth or Turborepo.
- The agent is a separate, credential-less Python service behind a trusted dispatcher (see "Agent service" below).
- Still open: the tech-stack doc specifies TanStack Router, shadcn/ui and AG Grid on the frontend, where the starter uses React Router and plain CSS. The FastAPI confirmation does not settle these.

Everything below describes the code as it exists today.

## Commands

Backend, from `backend/`. Needs Python 3.12 and `uv`:

```
uv sync
uv run ruff check .
uv run pytest
uv run pytest tests/test_rls.py::test_tenant_a_cannot_read_tenant_b   # single test
uv run pytest -k scope                                                # by name
uv run pytest -m db                                                   # database tests only
uv run alembic upgrade head
uv run python -m app.authz.seed                                       # after migrating, or after editing the YAML catalogue
uv run alembic revision -m "describe change"
uv run uvicorn app.main:app --reload
uv run python -m app.route_map > route-map.json
```

Frontend, from `frontend/`. Needs Node 22:

```
npm ci
npm run dev
npm test
npx vitest run src/permissions/can.test.ts   # single file
npm run typecheck
npm run build
```

Agent, from `agent/`. Needs Python 3.12 and `uv`:

```
uv sync
uv run ruff check .
uv run pytest
```

Whole stack: `cp .env.example .env`, then `docker compose up --build`. Add `--profile agent` for the agent services. Set `DISPATCHER_DB_PASSWORD` first: migrations need it.

`db` tests need a migrated, seeded PostgreSQL 16 and these variables (values in `.env.example`): `DATABASE_URL` (runtime role), `MIGRATION_DATABASE_URL` (owner role), `APP_DB_PASSWORD`, `DISPATCHER_DB_PASSWORD`. On Windows use `127.0.0.1` rather than `localhost` in the URLs: Docker publishes on IPv4 only, and each connection otherwise stalls for seconds. Without a reachable database they are **skipped** locally, so check the skip count before trusting a green run. `REQUIRE_DB_TESTS=1` turns that into a failure, as in CI.

## Architecture

**Request path.** A handler declares access as a parameter default: `ctx: TenantContext = require("member.read")`. The guard (`app/authz/guard.py`) authenticates, takes the tenant selector from the `{tenant_id}` path param or `X-Tenant-Id` header, resolves effective permissions, checks the key, and returns a `TenantContext`. The handler then opens `tenant_tx(ctx)`.

- Authorization and the handler run in separate transactions. Handlers commit explicitly inside `tenant_tx` so the commit precedes the response; do not switch to `yield` dependencies.
- Sensitive and critical decisions are audited in their own committed transaction, so a denial survives the request failing.
- `tenant_tx` accepts only a `TenantContext`. `scoped_tx(uuid)` is the lower-level form for paths where trust was established another way (membership lookup, signature-verified webhook source lookup).
- Non-member, nonexistent tenant and malformed selector all return the same 404. `403 MISSING_PERMISSION` only appears after membership is established.
- `authenticate` in `guard.py` resolves an opaque session cookie against current state on every request. Do not add a dev bypass; tests use `dependency_overrides`.

**Startup validation.** `app/bootstrap.py:finish_app` runs for every app and refuses to start on an undeclared route, unknown permission key, invalid manifest or prohibited agent tool. FastAPI wraps included routers, so `_api_routes` walks `original_router`. Unknown route types fail startup on purpose, and include-level prefixes are refused because they would falsify the route map: put prefixes on the `APIRouter`.

**Three ASGI apps.** `app/main.py` is public. `app/worker_api.py` holds the private Worker route and `app/agent_tools_api.py` the private `/internal/agent-tools` routes; each runs as its own process with no published port. Never mount internal routes on the public app, and never put agent tools on the Worker app.

**Agent service (§8).** `agent/` is a separate package with no backend code, database driver or database credentials. `POST /api/agent/investigate/{case_id}` writes a `RUNNING` row and a job in one transaction and returns 202. `app/investigation/dispatcher.py` claims the job with `FOR UPDATE SKIP LOCKED`, mints a token (`tokens.py`), calls the agent's `/run` and validates the output (`response.py`) before the single terminal write. The agent reads only through the tool API, whose routes are generated from the server-side registry. Tokens carry identity only, so POL-08 is resolved on every tool call. `test_investigation.py` and `test_code_rules.py` enforce this boundary.

The queue (`agent_jobs`) and the `settleiq_dispatcher` role, whose only cross-tenant access is claiming job rows, exist since migration 0004, and ontology generation already runs through them. Because only the agent holds the LLM key and `api` cannot reach it, every LLM call goes through this queue. Investigations are stubs (501 or `NotImplementedError`) until tasks 5.7 and 5.8, and the services only start with `--profile agent`. The investigation migration is deliberately not stubbed: it needs `investigations`, `investigation_tool_calls` and the write-once terminal trigger. An empty placeholder would be recorded as applied, and its later contents would never run.

**Authorization is data.** Permission metadata is loaded from `app/authz/permissions.yaml` into memory. Permission sets are seeded from `permission_sets.yaml` into PostgreSQL and read from there on every request; nothing is cached, so revocation applies on the next decision. Set names must not appear in code, and CI scans `app/`, `packs/`, `agent/settleiq_agent` and `frontend/src`. The owner-derived set is found by `derivation = 'OWNER_POINTER'`, not by name. Agent and Worker principals, platform access and pack-registered permission keys do not resolve yet.

**Database.** Two roles matter: the owner runs migrations, and `settleiq_app` (created by migration 0001, `NOSUPERUSER NOBYPASSRLS`) serves requests. Superusers bypass RLS, so tests must run as `settleiq_app`. Migrations are hand-written SQL with no autogenerate. A new tenant-owned table needs `enable_tenant_rls` from `app/migration_support.py` and an explicit `GRANT` to `settleiq_app`. Append-only tables get only `SELECT, INSERT`. `test_every_tenant_owned_table_has_forced_rls` catches a miss.

**Core versus packs.** `app/` is domain-neutral and reads `packs/*/manifest.yaml` as data; it must never import `packs`. Finance logic, including `Money`, belongs in `packs/finops`. Connector capability keys are canonical in `app/connectors/capability_registry.yaml`, and manifests declare unsupported capabilities explicitly.

**Test gotchas.** `test_route_guard.py` generates its tables from the route map, so new routes are covered automatically. `test_held_permission_passes_the_gate` accepts only 200 or 501 and uses a fake gateway, so implementing a handler that touches the database will need that test adjusted. `test_catalogue_matches_appendix_a_exactly` fails on any new core key by design.

## Rules for the work ahead

These bind code not yet written. The spec sections hold the detail.

- **Execution chain (§6.3, §9):** request, then immutable REQUEST decision, then approval if required, then immutable EXECUTION decision, then execution row, then single-use grant, then attempts. These stay separate tables with approval referencing the request. Case, request and execution state machines stay separate.
- **Request bodies (§12.1)** never accept `tenant_id`, `requested_by`, `approver_id`, `approval_required`, policy-decision IDs, `execution_id` or grants.
- **SHADOW (§9.5)** is the default per exact `(tenant, pack, connector, action)` scope and blocks all business-state changes. POL-14 attestation is checked at the LIVE switch and again at every grant.
- **Webhooks (§10.4):** verify the signature, then derive the tenant from the trusted source lookup, then write. An invalid signature writes nothing tenant-owned.
- **Agent (§8):** read, recommend and validate only, with append-only investigation writes. It computes no money and creates no request, approval or execution.
- **A case resolves only after independent readback of authoritative state,** never on API success.
- **Plan decisions:** PB-001 follows the conditional update in §4.4.1 rather than "replay the event". Break-glass, probabilistic matching and Redis/pgvector are not built in this slice. PB-002 live refund is built, in PayPal Sandbox only, under its own `refunds.create` attestation and safety suite. On Day 3 the order is freeze the build, create Tenant A, then attest; any later code change voids the attestation. If the safety suite is not green by Day 3 hour 7, no attestation is issued and the demo stops at step 10 in SHADOW. If only the refund scope is not green, no refund attestation is issued and the demo skips steps 14-15.
- **Draft contracts:** the error envelope, `X-Tenant-Id`, the connector manifest and the envelope are unratified defaults (listed in `README.md`).

## In-code TODOs

Gate 1 work is marked where it happens, as `TODO(G1 <task> <owner>): ...`. `<task>` is a plan task number (`1.9`), an OPEN item (`OPEN-14`) or `exit`; `<owner>` is the first name from the checklist below. "Owner review" TODOs mark starter code that its owner has yet to review.

```
git grep "TODO(G1 "              # all of Gate 1
git grep "TODO(G1 .* Rahul)"     # one person
git grep "TODO(G1 1.5 "          # one task
git grep "TODO(G1 .*agent"       # what the agent service (tasks 5.7, 5.8) needs from Gate 1
```

Delete a TODO in the same change that completes it. The checklist below stays the record of what is done. Keep permission-set names out of frontend TODOs: the role-name ban matches quoted names in comments too. Later gates can follow the same format (`TODO(G2 ...)`).

## Team checklist

Source: the delivery plan. Every task is unticked until its owner has reviewed it. Tick a box in the same change that completes the task, and only when automated evidence exists; a manual happy-path run does not count (§22.3). "Starter:" notes record what already exists in the FastAPI code. Hours are the plan's budget; each person has 24, and the plan is currently over that (see its "Over capacity" note).

### Rishav Chowdhury: Platform and Trust (leads G1, G5; demo Tenant Owner)

- [x] Resolve the backend stack conflict with the team (added; blocked backend work). FastAPI confirmed by Swapnaneel, 2026-11-09.
- [x] OPEN-REL-01: attestation identity and key custody (G1, Day 1). Decided in spec v1.6 §9.7, approved by Rishav 2026-11-09. The build is task 5.4; OPEN-REL-02 stays open.
- [x] OPEN-21-19: no permission cache in this build (G1, Day 1). Recorded in `README.md`; `test_revoked_grant_stops_on_next_decision` is the evidence.
- [x] 1.1 Repo, Docker Compose, CI skeleton; settle stack gaps (1.5h). Full stack run with `--profile agent`; `/readyz` added; production DNS, billing and instance checked.
- [ ] 1.1 follow-ups that need production or an AWS decision: first run of the hardened `deploy.yml` and `infra.yml` on `prod`; off-machine backup of `infra/bootstrap/terraform.tfstate`; deactivating the apply key.
- [x] 1.2 Identity and tenant migrations, RLS baseline, transaction-local tenant context (2h). Reviewed; `tests/test_rls.py` passes against PostgreSQL with no skips.
- [x] 1.3 Permission catalogue as seed data, deny-by-default route guard, CI route map, role-name ban (2.5h). Reviewed per key against §3.3 and §3.5; `test_route_guard.py`, `test_catalogue.py` and `test_code_rules.py` are the evidence.
- [x] 1.4 Owner pointer, atomic ownership transfer, `audit_logs` (1.5h). `tests/test_ownership.py` covers AC-OWNER-01, 03, 04, 05, including concurrent transfers.
- [x] 1.5 Invitation backend: hashed single-use tokens, matching-email acceptance (1.5h). `tests/test_invitations.py` covers AC-IAM-02 to 06, 08, 09, 10, AC-OWNER-02, AC-AUTHZ-05, 09, 18.
- [ ] 2.7 Atomic immutable publication; stale or concurrent attempts fail closed; audit (2h)
- [ ] 3.7 RLS and foreign keys on domain tables, `NUMERIC(20,6)`, cross-tenant negative tests (2h)
- [ ] 4.5 Secret references; credential and webhook routes limited to Integration Admin (2h)
- [ ] 5.1 Request, decision, approval, execution, grant and attempt tables with the full FK chain (2h)
- [ ] 5.2 Two-stage policy gateway, POL-01, single-use grant issuance (2h)
- [ ] 5.3 Exact-scope execution mode, SHADOW by default (1h)
- [ ] 5.4 Signed POL-14 attestation job, checked at LIVE switch and at grant issue (2h)
- [ ] 5.14 Gateway and execution mode for `refunds.create`: PayPal scope independent of PB-001, POL-14 checked per capability (1h)
- [ ] 6.3 Build freeze, create Tenant A, issue exact-scope attestation, check audit lineage (1.5h)
- [ ] 6.8 Issue the separate `refunds.create` exact-scope attestation; check audit lineage (0.5h)
- [ ] 6.7 Full team rehearsal (0.5h)

### Rahul Pandey: Integrations and Execution (leads G4; demo Integration Admin)

- [ ] OPEN-01, OPEN-04, OPEN-CAP-01, OPEN-PP-01 (Day 1, first hours)
- [ ] 4.1 **First task on Day 1:** Sandbox app, public HTTPS tunnel, verify provider access (1.5h)
- [x] 1.6 Freeze connector manifest, capability keys and source envelope (1h). Evidence in `test_contracts.py`.
- [x] 1.7 Source registry, `source_records`, raw evidence store with hashing (3h). Evidence in `test_sources.py`.
- [ ] 2.5 PostgreSQL schema discovery and profiling (2h)
- [ ] 2.6 CSV metadata discovery (1h)
- [ ] 3.5 Merchant PostgreSQL read and normalization (1h)
- [ ] 3.6 Bank CSV and BRR parser on the shared pipeline, with provenance mode (2h)
- [ ] 4.2 OAuth, capture details, Transaction Search backfill, adapter manifest (2h). Starter: manifest draft exists.
- [ ] 4.3 Webhook route: signature check, trusted source lookup, tenant derivation, inbox, dedupe (3h)
- [ ] 4.4 Async processing, retry, replay and dead-letter state (1.5h)
- [ ] 4.9 Sandbox duplicate-capture setup: two successful capture IDs for one intended order (0.5h)
- [ ] 4.10 PayPal `refunds.create` and `refunds.read` adapter: versioned parameter contract, exact capture target, provider idempotency key, Sandbox only (2.5h). Starter: capability keys declared in the manifest draft.
- [ ] 5.5 Internal-merchant adapter: conditional update, idempotency, restricted DB role (2.5h). Starter: manifest draft exists.
- [ ] 5.6 Worker: atomic grant consumption, durable attempts, independent readback (2.5h)
- [ ] 5.15 Worker refund execution: grant-bound, durable attempts, no blind retry on an ambiguous outcome, verified completion by readback (1.5h)
- [ ] 6.2 Non-financial source through the generic pipeline (AC-OPEN-01/02/06) (0.5h)
- [ ] 6.7 Full team rehearsal (0.5h)

### Swapnaneel Ray: Semantics, Domain and AI (leads G2, G3; demo Domain Operator, also holds an Approver grant for the POL-01 denial)

- [x] OPEN-03 (Day 1). Recorded in `README.md`; activation rules are tested in `test_contracts.py`.
- [ ] OPEN-10: included-in-net semantics for the fixture (before 3.3; not a G1 item)
- [x] 1.8 Freeze domain-pack manifest (0.5h). Evidence in `test_contracts.py`.
- [x] 1.9 Registration, email verification, email sending, atomic tenant creation (2.5h). Evidence in `test_identity.py`.
- [x] 1.9 follow-up: configure an SMTP relay and confirm a real email arrives. Brevo is configured locally, and a real test email arrived on 2026-11-10. Production still needs its SMTP values in `.env.production`.
- [ ] 2.1 Ontology composition store; platform core and Finance pack in JSON-LD (2h)
- [ ] 2.2 LLM draft generator from discovered metadata, draft only (2h)
- [ ] 2.3 SHACL, pack validators, hash-bound validation, `confirm_publish` (2h)
- [ ] 2.4 Versioned mappings and authority assertions, including two sources to one property (1h)
- [ ] 3.1 Domain projection and exact-ID identity resolution; ambiguous candidates stay reviewable (1.5h)
- [ ] 3.2 Property observations, resolved views, explicit authority conflicts (1.5h)
- [ ] 3.3 Finance engine: both reconciliation scopes, settlement calculation, decimal-safe money (2h). Starter: `Money` type built.
- [ ] 3.4 Version-pinned `domain_runs`, case lifecycle (1h)
- [ ] 3.10 `DUPLICATE_CAPTURE` detection and fixture: two distinct successful captures for one intended order; equal amounts alone never qualify (1.5h)
- [ ] 4.6 PayPal field mappings and capture-completion authority (1h)
- [ ] 5.7 AI orchestrator: 6 tools, POL-08 permission intersection, durable tool trace (3h). Starter: tool registry declared; agent service, dispatcher, tokens and tool API stubbed; migration not started.
- [ ] 5.8 Agent response schema and prohibited-tool registry check (1h). Starter: prohibited-tool check built; draft response schema, evidence check stubbed.
- [ ] 5.9 PB-001 definition and the request-lifecycle state machine (1h)
- [ ] 5.13 PB-002 definition and agent recommendation: duplicate proof, currency and amount, no prior refund or hold, always-required independent approval (1h)
- [ ] 6.1 SaaS pack: manifest, one validator, renewal/invoice fixture (1.5h). Starter: manifest stub exists.
- [ ] 6.7 Full team rehearsal (0.5h)

### Suprava Dutta: Product Experience and QA (leads G6; demo Approver)

- [ ] OPEN-14: API conventions, auth transport, error envelope (Day 1, first hours). Starter: draft envelope and `X-Tenant-Id`.
- [ ] 1.10 Freeze OpenAPI, adding the routes §12.2 omits (1h). Starter: §12.2 routes exist; omitted ones do not.
- [ ] 1.11 App shell, sign-up and sign-in, tenant creation, permission-driven UI (2h). Starter: shell and permission nav only.
- [ ] 1.12 Team Management screens (1h)
- [ ] 1.13 IAM and cross-tenant tests (1h). Starter: baseline RLS and resolution tests exist.
- [ ] 2.8 Ontology Studio graph in React Flow, view and edit relationships (2.5h). Starter: empty canvas.
- [ ] 2.9 Binding and authority editors (1.5h)
- [ ] 2.10 Publish confirmation showing candidate hash and validation run (1h)
- [ ] 3.8 SYNTHETIC/REFERENCE BRR fixture with manifest, version and checksum (1h)
- [ ] 3.9 Domain Operations screens: cases, observations, resolved values (2h)
- [ ] 4.7 Source Management screens, including credential status and SYNTHETIC label (1.5h)
- [ ] 4.8 Failure tests: invalid signature, duplicate and out-of-order events (0.5h)
- [ ] 5.10 Investigation screens (1.5h)
- [ ] 5.11 Playbook Requests screens (1.5h)
- [ ] 5.12 Safety suite: AC-MERCH-01..07, AC-AUTHZ-06/08/15/16/17, AC-PLY-08..11 (2h)
- [ ] 5.16 PB-002 request and approval screens showing the duplicate proof (1h)
- [ ] 5.17 Refund safety suite: AC-PP-08, AC-PLY-02, PB-002 parts of AC-PLY-08, AC-EXEC-MODE-03, all required capability categories (1.5h)
- [ ] 6.4 Tenant B isolation test (1h)
- [ ] 6.5 Acceptance-criteria status sheet with owners and evidence, §22.3 (0.5h)
- [ ] 6.6 Demo script with SYNTHETIC disclosure and the list of what was not built (2h)
- [ ] 6.9 Extend the demo script and rehearsal to steps 14-15 (0.5h)
- [ ] 6.7 Full team rehearsal (0.5h)
