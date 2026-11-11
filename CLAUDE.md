# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

SettleIQ is a multi-tenant, ontology-driven business operations platform; Financial Operations with PayPal Sandbox is the first domain.

## Source documents

- `SettleIQ_Authoritative_Specification_v1.7.md` is the sole normative reference. It is 1,615 lines, so grep for the ID you need (`POL-09`, `AC-MERCH-05`, `§9.3`). Requirement changes need a new integrated revision (§22.2), not a sidecar note.
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
uv run python -m app.openapi_export > openapi.json                   # after a deliberate API contract change
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

**Three ASGI apps.** `app/main.py` is public. `app/worker_api.py` holds the private Worker route, which authenticates only a bearer execution grant (`app/authz/workers.py`), never a session cookie and `app/agent_tools_api.py` the private `/internal/agent-tools` routes; each runs as its own process with no published port. Never mount internal routes on the public app, and never put agent tools on the Worker app. `python -m app.webhooks.run` (compose service `webhooks`, no port) drains the webhook inbox: the public webhook route only verifies and stores, and the processor records the event and starts each active pack's run as the tenant's Worker principal.

**Agent service (§8).** `agent/` is a separate package with no backend code, database driver or database credentials. `POST /api/agent/investigate/{case_id}` writes a `RUNNING` row and a job in one transaction and returns 202. `app/investigation/dispatcher.py` claims the job with `FOR UPDATE SKIP LOCKED`, mints a token (`tokens.py`), calls the agent's `/run` and validates the output (`response.py`) before the single terminal write. The agent reads only through the tool API, whose routes are generated from the server-side registry. Tokens carry identity only, so POL-08 is resolved on every tool call. `test_investigation.py` and `test_code_rules.py` enforce this boundary.

The queue (`agent_jobs`) and the `settleiq_dispatcher` role, whose only cross-tenant access is claiming job rows, exist since migration 0004, and ontology generation already runs through them. Because only the agent holds the LLM key and `api` cannot reach it, every LLM call goes through this queue. Investigations run through it as INVESTIGATION jobs, and the agent services only start with `--profile agent`, which also needs `AGENT_TOKEN_SECRET` (at least 32 characters) shared by `dispatcher` and `agent-tools`. The dispatcher sends the agent the tool specs with each run, so the agent never keeps its own copy. The dispatcher role may update only the terminal columns of `investigations`, and a trigger freezes the row once it is terminal.

**Authorization is data.** Permission metadata is loaded from `app/authz/permissions.yaml` into memory. Permission sets are seeded from `permission_sets.yaml` into PostgreSQL and read from there on every request; nothing is cached, so revocation applies on the next decision. Set names must not appear in code, and CI scans `app/`, `packs/`, `agent/settleiq_agent` and `frontend/src`. The owner-derived set is found by `derivation = 'OWNER_POINTER'`, not by name. Agent and Worker principals, platform access and pack-registered permission keys do not resolve yet.

**Database.** Two roles matter: the owner runs migrations, and `settleiq_app` (created by migration 0001, `NOSUPERUSER NOBYPASSRLS`) serves requests. Superusers bypass RLS, so tests must run as `settleiq_app`. Migrations are hand-written SQL with no autogenerate. A new tenant-owned table needs `enable_tenant_rls` from `app/migration_support.py` and an explicit `GRANT` to `settleiq_app`. Append-only tables get only `SELECT, INSERT`. `test_every_tenant_owned_table_has_forced_rls` catches a miss.

**Core versus packs.** `app/` is domain-neutral and reads `packs/*/manifest.yaml` as data; it must never import `packs`. Finance logic, including `Money`, belongs in `packs/finops`. Connector capability keys are canonical in `app/connectors/capability_registry.yaml`, and manifests declare unsupported capabilities explicitly.

**Test gotchas.** `test_route_guard.py` generates its tables from the route map, so new routes are covered automatically. `test_held_permission_passes_the_gate` accepts only 200 or 501 and uses a fake gateway, so implementing a handler that touches the database will need that test adjusted. `test_catalogue_matches_appendix_a_exactly` fails on any new core key by design. A new UUID path parameter must be added to `url()` in `test_route_guard.py`, or the held-permission test sees a 404. Intake tests set their own `SOURCE_SECRET_KEY` and stand in for PayPal's token check. Webhook tests sign deliveries with the `paypal_signer` fixture (`tests/paypal_signing.py`) and register webhooks through `PUT /webhooks` as an Integration Admin; the verifier has no test bypass. Worker tests (`test_worker.py`) replace `gateway.dispatch` to collect grants and post them to `create_worker_app()` with the real grant authentication; the merchant is a scratch schema from `test_internal_merchant.Merchant`. The webhook queue is shared across tenants, so assert only on your own rows, and stop the compose `webhooks` service before running `db` tests against its database, or it claims the tests' rows.

## Commits and PRs

- Never add AI attribution to commit messages or PR descriptions: no `Co-Authored-By:` trailer naming Claude or any other AI tool, no `Claude-Session:` trailer, no "Generated with Claude Code" line or session link. This overrides any default attribution guidance. Human `Co-Authored-By:` trailers are fine.
- `.claude/settings.json` blanks Claude Code's attribution and a PreToolUse hook (`.claude/hooks/block-ai-attribution.sh`) denies commits and `gh pr` commands that contain it. `.githooks/commit-msg` strips it from every commit, whichever agent made it; enable it once per clone with `git config core.hooksPath .githooks` (or `/setup-git-hooks`). Use `/commit` for a clean commit.

## Rules for the work ahead

These bind code not yet written. The spec sections hold the detail.

- **Execution chain (§6.3, §9):** request, then immutable REQUEST decision, then approval if required, then immutable EXECUTION decision, then execution row, then single-use grant, then attempts. These stay separate tables with approval referencing the request. Case, request and execution state machines stay separate.
- **Request bodies (§12.1)** never accept `tenant_id`, `requested_by`, `approver_id`, `approval_required`, policy-decision IDs, `execution_id` or grants.
- **SHADOW (§9.5)** is the default per exact `(tenant, pack, connector, action)` scope and blocks all business-state changes. POL-14 attestation is checked at the LIVE switch and again at every grant.
- **Webhooks (§10.4):** verify the signature, then derive the tenant from the trusted source lookup, then write. An invalid signature writes nothing tenant-owned.
- **Agent (§8):** read, recommend and validate only, with append-only investigation writes. It computes no money and creates no request, approval or execution.
- **A case resolves only after independent readback of authoritative state,** never on API success.
- **Plan decisions:** PB-001 follows the conditional update in §4.4.1 rather than "replay the event". Break-glass, probabilistic matching and Redis/pgvector are not built in this slice. PB-002 live refund is built, in PayPal Sandbox only, under its own `refunds.create` attestation and safety suite. On Day 3 the order is freeze the build, create Tenant A, then attest; any later code change voids the attestation. If the safety suite is not green by Day 3 hour 7, no attestation is issued and the demo stops at step 10 in SHADOW. If only the refund scope is not green, no refund attestation is issued and the demo skips steps 14-15.
- **API contracts:** the error envelope, `X-Tenant-Id`, the session-cookie transport and `/api` as version 1 are ratified under OPEN-14 (listed in `README.md`). The connector manifest (OPEN-01, OPEN-CAP-01) and the source-record envelope (OPEN-04) are decided for this build; adapters follow `app/sources/conventions.py` for API reads, events and files.

## In-code TODOs

Gates 1 to 4 are closed: every `TODO(G1 ...)` to `TODO(G4 ...)` marker has been removed and their checklist items are ticked. Later gates mark work where it happens, as `TODO(G<n> <task> <owner>): ...`. `<task>` is a plan task number (`2.7`), an OPEN item (`OPEN-10`) or `exit`; `<owner>` is the first name from the checklist below. "Owner review" TODOs mark starter code that its owner has yet to review.

```
git grep "TODO(G5 "              # all of Gate 5
git grep "TODO(G5 .* Rahul)"     # one person
git grep "TODO(G5 5.2 "          # one task
```

Delete a TODO in the same change that completes it. The checklist below stays the record of what is done. Keep permission-set names out of frontend TODOs: the role-name ban matches quoted names in comments too.

## Team checklist

Source: the delivery plan. Every task is unticked until its owner has reviewed it. Tick a box in the same change that completes the task, and only when automated evidence exists; a manual happy-path run does not count (§22.3). "Starter:" notes record what already exists in the FastAPI code. Hours are the plan's budget; each person has 24, and the plan is currently over that (see its "Over capacity" note).

### Rishav Chowdhury: Platform and Trust (leads G1, G5; demo Tenant Owner)

- [x] Resolve the backend stack conflict with the team (added; blocked backend work). FastAPI confirmed by Swapnaneel, 2026-11-09.
- [x] OPEN-REL-01: attestation identity and key custody (G1, Day 1). Decided in spec v1.6 §9.7, approved by Rishav 2026-11-09. The build is task 5.4; OPEN-REL-02 stays open.
- [x] OPEN-21-19: no permission cache in this build (G1, Day 1). Recorded in `README.md`; `test_revoked_grant_stops_on_next_decision` is the evidence.
- [x] 1.1 Repo, Docker Compose, CI skeleton; settle stack gaps (1.5h). Full stack run with `--profile agent`; `/readyz` added; production DNS, billing and instance checked.
- [x] 1.1 follow-ups: the hardened workflows ran on `prod` (infra run 42892572082 applied after tests, deploy run 42892572374 rolled back a failed release, deploy run 42894875467 went live); `infra/bootstrap/terraform.tfstate` backed up off-machine; apply key deactivated. Confirmed on `prod` 2026-11-11.
- [x] 1.2 Identity and tenant migrations, RLS baseline, transaction-local tenant context (2h). Reviewed; `tests/test_rls.py` passes against PostgreSQL with no skips.
- [x] 1.3 Permission catalogue as seed data, deny-by-default route guard, CI route map, role-name ban (2.5h). Reviewed per key against §3.3 and §3.5; `test_route_guard.py`, `test_catalogue.py` and `test_code_rules.py` are the evidence.
- [x] 1.4 Owner pointer, atomic ownership transfer, `audit_logs` (1.5h). `tests/test_ownership.py` covers AC-OWNER-01, 03, 04, 05, including concurrent transfers.
- [x] 1.5 Invitation backend: hashed single-use tokens, matching-email acceptance (1.5h). `tests/test_invitations.py` covers AC-IAM-02 to 06, 08, 09, 10, AC-OWNER-02, AC-AUTHZ-05, 09, 18.
- [x] 2.7 Atomic immutable publication; stale or concurrent attempts fail closed; audit (2h). Evidence in `test_publication.py` and `test_mappings.py`.
- [x] 3.7 RLS and foreign keys on domain tables, `NUMERIC(20,6)`, cross-tenant negative tests (2h). Migration 0011; evidence in `test_rls.py` (domain section), traced under AC-TEN-02.
- [x] 4.5 Secret references; credential and webhook routes limited to Integration Admin (2h). `env:` and `managed:` references (the second rendered from SSM by `deploy/deploy.sh`); evidence in `test_source_credentials.py` (AC-PP-10 both sides) and `test_secrets.py`.
- [x] 4.5 follow-up: tenant-namespaced secret references (spec v1.7 §11.3, AC-PP-13). Built 2026-11-11, reviewed and accepted by Rishav 2026-11-12: `managed:<NAME>` resolves under `<SOURCE_SECRET_DIR>/<tenant_id>/`, rendered from SSM `/source/<tenant_id>/<NAME>`, and production refuses `env:`. Evidence in `test_secrets.py` and `test_source_credentials.py` (`test_a_reference_names_a_secret_in_its_own_tenant_only`). Any SSM parameter still at the flat `/source/<NAME>` path must move under its tenant before the next deploy.
- [x] 4.11 Write-only credential intake (spec v1.7 §11.3, AC-PP-14; OPEN-PP-03 decided for this build in `README.md`). Built 2026-11-11, reviewed and accepted by Rishav 2026-11-12: `PUT /api/sources/{id}/credential-value` checks PayPal credentials against Sandbox, seals them under `SOURCE_SECRET_KEY` (api and, since 5.15, the Worker) in `source_credential_values` (migration 0015) and stores `stored:<source_id>`; the Sources card has **Connect your PayPal app**. Evidence in `test_credential_intake.py` and `sources.test.tsx`; used live on 2026-11-11 to connect the team's Sandbox app for the G4 live exit (`README.md`).
- [x] 5.1 Request, decision, approval, execution, grant and attempt tables with the full FK chain (2h). Migration 0016 (`executions` per spec §6.3), with triggers for the conditional approval, POL-01, single-use grants and both §9.3 state machines; tenant approval policy is data in `playbook_approval_policies` (absent means REQUIRED). Evidence in `test_execution_chain.py` (AC-PLY-09, 10, 11).
- [x] 5.2 Two-stage policy gateway, POL-01, single-use grant issuance (2h). `app/playbooks/gateway.py` behind the four `api/playbooks.py` routes:
  - **Request.** Requires an `Idempotency-Key` header (OPEN-14 in `README.md`). It writes the request, the REQUEST decision from tenant policy and the case move to ACTION_PENDING in one transaction.
  - **Approve and reject.** POL-01 applies, and the requester's attempt is audited as a denial.
  - **Execution stage.** It revalidates the playbook and pack versions, the connector action, the case, the preconditions and the exact target, then checks the exact-scope mode and POL-14 and writes a separate EXECUTION decision: DENY, POLICY_BLOCKED or ALLOW.
  - **Grant.** After ALLOW, a single-use grant is issued in its own transaction, which checks the gate again and stores only the token hash. The Worker hand-off is the 5.6 hook.
  - **LIVE switch.** Switching a scope to LIVE re-evaluates the requests that scope blocked.
  - **Audit.** `write_audit` now records the permission, decision and policy IDs.

  Evidence in `test_playbook_gateway.py` (AC-PLY-01, 04, 08, 09; AC-AUTHZ-04, 08, 14, 15; AC-EXEC-MODE-04; AC-REL-11).
- [x] 5.3 Exact-scope execution mode, SHADOW by default (1h). Migration 0018 (`execution_mode_settings`, one row per exact scope, LIVE only with an attestation ID); `app/execution/scope.py` accepts only a scope the active pack, the connector manifest and the capability registry all declare; `PUT /api/tenants/{tenant_id}/execution-mode` asks the POL-14 verifier for LIVE and audits switches and refusals. Evidence in `test_execution_mode.py` (AC-EXEC-MODE-01, 02).
- [x] 5.4 Signed POL-14 attestation job, checked at LIVE switch and at grant issue (2h). Spec §9.7 built in the repo: a capability manifest packaged in the image and checked at startup (`app/release/capability_manifest.py`); RFC 8785 and DSSE with Ed25519 (`canonical.py`, `dsse.py`); the ten-check verifier (`verifier.py`), used by the LIVE switch and `GET /execution-readiness`; the running digest only from the `runtime-identity` service; signed, chained revocation states with a high-water mark (migration 0019); the `attest.yml` workflow with the standalone signer (`.github/attest/sign.py`); and the Object Lock bucket in Terraform. Evidence in `test_release_attestation.py` (AC-REL-01..05, 07..10, AC-AUTHZ-16) and `test_attest_workflow.py` (AC-REL-06). **Provisioning pending:** the GitHub environments, the signing key, the `keys.json` entry and `terraform apply`; the runbook is "Release attestation" in `README.md`. Until then every scope stays SHADOW. The grant-issue check is wired in by 5.2.
- [x] 5.14 Gateway and execution mode for `refunds.create`: PayPal scope independent of PB-001, POL-14 checked per capability (1h). `playbook_scope` in `app/execution/scope.py` maps each playbook to the one exact scope its definition names: PB-001 to `(finops, internal-merchant, merchant.payment_status.correct)`, PB-002 to `(finops, paypal, refunds.create)`. Evidence in `test_execution_mode.py` (AC-EXEC-MODE-03, both directions, LIVE and back to SHADOW) and `test_release_attestation.py` (AC-REL-12: neither capability's attestation authorizes the other or another tenant).
- [ ] 6.3 Build freeze, create Tenant A, issue exact-scope attestation, check audit lineage (1.5h)
- [ ] 6.8 Issue the separate `refunds.create` exact-scope attestation; check audit lineage (0.5h)
- [ ] 6.7 Full team rehearsal (0.5h)

### Rahul Pandey: Integrations and Execution (leads G4; demo Integration Admin)

- [x] OPEN-01, OPEN-04, OPEN-CAP-01, OPEN-PP-01 (Day 1, first hours). Recorded in `README.md`, with evidence in `test_contracts.py` and `test_sources.py`. The Sandbox account checks for OPEN-PP-01 continue under G4 task 4.1.
- [x] 4.1 **First task on Day 1:** Sandbox app, public HTTPS tunnel, verify provider access (1.5h). Evidence in `scripts/verify_paypal_sandbox.py` and `README.md` (OPEN-PP-01).
- [x] 1.6 Freeze connector manifest, capability keys and source envelope (1h). Evidence in `test_contracts.py`.
- [x] 1.7 Source registry, `source_records`, raw evidence store with hashing (3h). Evidence in `test_sources.py`.
- [x] 2.5 PostgreSQL schema discovery and profiling (2h). Evidence in `test_discovery.py`, `test_profiling.py` and `test_secrets.py`; the merchant read source uses `env:SOURCE_SECRET_*` references until 4.5.
- [x] 2.6 CSV metadata discovery (1h). Evidence in `test_discovery.py`; discovery reads the hash-checked raw-evidence copy, and the upload route stays with 3.6.
- [x] 3.5 Merchant PostgreSQL read and normalization (1h). Evidence in `test_sync.py`; reads discovered tables only, preserves every column and decimal precision, and never writes to the merchant.
- [x] 3.6 Bank CSV and BRR parser on the shared pipeline, with provenance mode (2h). Evidence in `test_ingestion.py`; provider reports, SYNTHETIC fixtures and tenant files share one path, with provenance on the raw evidence.
- [x] 4.2 OAuth, capture details, Transaction Search backfill, adapter manifest (2h). Evidence in `test_paypal_g4.py` (AC-PP-01, AC-PP-02).
- [x] 4.3 Webhook route: signature check, trusted source lookup, tenant derivation, inbox, dedupe (3h). Evidence in `test_paypal_g4.py` (AC-WEBHOOK-01, AC-PP-03, AC-PP-04).
- [x] 4.4 Async processing, retry, replay and dead-letter state (1.5h). Evidence in `test_paypal_g4.py` (AC-PP-05, AC-PP-06).
- [x] G4 exit with a real Sandbox `PAYMENT.CAPTURE.COMPLETED` (2026-11-11): PayPal's own delivery through a tunnel was verified, `PROCESSED`, started a Worker run and reconciled to `STATUS_MISMATCH`. Recorded under "G4 live exit step" in `README.md`; the automated evidence is `test_g4_exit.py`.
- [x] 4.9 Sandbox duplicate-capture setup: two successful capture IDs for one intended order (0.5h). Evidence in `test_paypal_g4.py` (AC-PLY-02).
- [x] 4.10 PayPal `refunds.create` and `refunds.read` adapter: versioned parameter contract, exact capture target, provider idempotency key, Sandbox only (2.5h). Evidence in `test_paypal_g4.py` (AC-PP-12).
- [x] 5.5 Internal-merchant adapter: conditional update, idempotency, restricted DB role (2.5h). `app/connectors/internal_merchant/` with the merchant-side `merchant_setup.sql` (view, action-record table, a role that can only update `status`), registered in `IMPLEMENTATIONS` and the capability manifest. Evidence in `test_internal_merchant.py` (AC-MERCH-01..07 at the adapter).
- [x] 5.6 Worker: atomic grant consumption, durable attempts, independent readback (2.5h). The api posts each grant to the Worker as a bearer token (`gateway._to_worker`, `WORKER_URL`); the Worker app authenticates only that grant (`app/authz/workers.py`), and `app/execution/worker.py` re-checks binding, mode and POL-14, consumes the grant with the move to EXECUTING, records each phase as an attempt and resolves the case only on a matching readback. `GET /api/executions/{id}` returns the attempts and readback. Evidence in `test_worker.py` (AC-MERCH-01/02 end to end, AC-AUTHZ-08/15/16/17, AC-PLY-06/10) and `test_rls.py`.
- [x] 5.15 Worker refund execution: grant-bound, durable attempts, no blind retry on an ambiguous outcome, verified completion by readback (1.5h). `app/execution/refunds.py` on the 5.6 grant and attempt rules: both captures are read before refunding, an unknown outcome is settled by readback and repeated only under the same `PayPal-Request-Id`, and the case resolves only when the refund reads COMPLETED against the duplicate with the original untouched. `create_refund` no longer reads a REFUNDED capture as success. The Worker now holds `SOURCE_SECRET_KEY` with the api. Evidence in `test_worker_refunds.py` (AC-PP-08, ambiguous outcomes, SHADOW, tenant isolation, key custody).
- [ ] 6.2 Non-financial source through the generic pipeline (AC-OPEN-01/02/06) (0.5h)
- [ ] 6.7 Full team rehearsal (0.5h)

### Swapnaneel Ray: Semantics, Domain and AI (leads G2, G3; demo Domain Operator, also holds an Approver grant for the POL-01 denial)

- [x] OPEN-03 (Day 1). Recorded in `README.md`; activation rules are tested in `test_contracts.py`.
- [x] OPEN-10: included-in-net semantics for the fixture (before 3.3; not a G1 item). Recorded in `README.md`; the table is `packs/finops/settlement_treatment.yaml` and `test_settlement.py` is the evidence.
- [x] 1.8 Freeze domain-pack manifest (0.5h). Evidence in `test_contracts.py`.
- [x] 1.9 Registration, email verification, email sending, atomic tenant creation (2.5h). Evidence in `test_identity.py`.
- [x] 1.9 follow-up: configure an SMTP relay and confirm a real email arrives. Brevo is configured locally, and a real test email arrived on 2026-11-10. Production still needs its SMTP values in `.env.production`.
- [x] 2.1 Ontology composition store; platform core and Finance pack in JSON-LD (2h). Evidence in `test_semantics_store.py` and `test_publication.py`.
- [x] 2.2 LLM draft generator from discovered metadata, draft only (2h). Evidence in `test_generation.py`; real metadata waits on Rahul's 2.5.
- [x] 2.3 SHACL, pack validators, hash-bound validation, `confirm_publish` (2h). Evidence in `test_validation.py` and `test_publication.py`.
- [x] 2.4 Versioned mappings and authority assertions, including two sources to one property (1h). Evidence in `test_mappings.py`.
- [x] 3.1 Domain projection and exact-ID identity resolution; ambiguous candidates stay reviewable (1.5h). Policy is data in `packs/finops/resolution.yaml`; evidence in `test_domain_projection.py`.
- [x] 3.2 Property observations, resolved views, explicit authority conflicts (1.5h). Evidence in `test_domain_observations.py`.
- [x] 3.3 Finance engine: both reconciliation scopes, settlement calculation, decimal-safe money (2h). `packs/finops/engine.py` over the OPEN-10 calculation; evidence in `test_reconciliation.py` and `test_settlement.py`, which also cover the G3 exit criteria.
- [x] 3.4 Version-pinned `domain_runs`, case lifecycle (1h). Adds `GET /api/domain-runs` and `GET /api/entity-resolution` for 3.9; evidence in `test_domain_runs.py` and `test_case_lifecycle.py`.
- [x] 3.10 `DUPLICATE_CAPTURE` detection and fixture: two distinct successful captures for one intended order; equal amounts alone never qualify (1.5h). Fixture `seed_reconciliation` in `tests/domain_fixture.py`; evidence in `test_reconciliation.py`.
- [x] 4.6 PayPal field mappings and capture-completion authority (1h). Typed `mapping_hints` in `packs/finops/manifest.yaml`, applied with `hint_bodies`; event records match mappings by event type. Evidence in `test_paypal_mappings.py` (AC-PP-06 from a `PAYMENT.CAPTURE.COMPLETED` event), `test_contracts.py` and `test_domain_projection.py`.
- [x] 5.7 AI orchestrator: all 11 registry tools (spec 8.2), POL-08 permission intersection, durable tool trace (5h). Migration 0017 (`investigations`, write-once at its terminal state, and append-only `investigation_tool_calls`); `POST /api/agent/investigate/{case_id}` writes the RUNNING row and its job; the dispatcher mints an HMAC token (`tokens.py`), calls the agent and validates its answer against the server-written trace before the single terminal write. The tool API (`tool_guard.py`, `tool_impl.py`) resolves POL-08 on every call and scopes every argument to the case snapshot; `calculate_settlement_variance` is pack code reached through the registry. The agent (`agent/settleiq_agent/loop.py`) is an OpenAI function-calling loop bounded by `MAX_STEPS` and `DEADLINE_SECONDS`. Evidence in `test_investigation_run.py` (AC-INV-01, AC-AI-01/03/04/05, POL-08 revocation mid-run, write-once) and `agent/tests/test_loop.py`.
- [x] 5.8 Agent response schema and prohibited-tool registry check (1h). `app/investigation/response.py` freezes the §8.3 schema and `validate_response` refuses output that names another case, an unregistered classification, an ineligible playbook, the wrong approval requirement or any evidence ID no tool returned; the agent mirrors it in `agent/settleiq_agent/response_schema.json`. Evidence in `test_investigation.py` (AC-AI-02, AC-AI-04, AC-AGENT-01/02) and `agent/tests/test_contract.py`.
- [x] 5.9 PB-001 definition and the request-lifecycle state machine (1h). Definition schema in `app/domain_packs/playbooks.py`, validated at startup; PB-001 is `packs/finops/playbook_definitions/pb-001.yaml` with preconditions in `packs/finops/playbooks.py`, read from a case evidence snapshot (`app/playbooks/evidence.py`); the machine is `app/playbooks/lifecycle.py`. Evidence in `test_playbook_definitions.py` and `test_request_lifecycle.py` (Python and trigger agree; PB-001 eligible on a real COMPLETED event against a PENDING merchant row).
- [x] 5.13 PB-002 definition and agent recommendation: duplicate proof, currency and amount, no prior refund or hold, always-required independent approval (1h). `packs/finops/playbook_definitions/pb-002.yaml` (`ALWAYS_REQUIRED`) with preconditions in `packs/finops/playbooks.py`; recommendation and approval policy in `app/playbooks/policy.py`. Evidence in `test_pb002.py` (AC-PLY-02, prior refund, dispute, ambiguous original, PB-002 part of AC-PLY-08).
- [ ] 6.1 SaaS pack: manifest, one validator, renewal/invoice fixture (1.5h). Starter: manifest stub exists.
- [ ] 6.7 Full team rehearsal (0.5h)

### Suprava Dutta: Product Experience and QA (leads G6; demo Approver)

- [x] OPEN-14: API conventions, auth transport, error envelope (Day 1, first hours). Envelope, `X-Tenant-Id`, the session cookie and `/api` as version 1 are ratified in `README.md`; evidence in `test_route_guard.py` (canonical 401, 404 and 422, `API-Version`). Pagination and idempotency headers wait for the first routes that need them.
- [x] 1.10 Freeze OpenAPI, adding the routes §12.2 omits (1h). Added suspend and reinstate (under `member.remove`), entity reads and the 202 investigation body; `backend/openapi.json` is frozen by `test_openapi.py`.
- [x] 1.11 App shell, sign-up and sign-in, tenant creation, permission-driven UI (2h). Evidence in `session.test.tsx`, `tenants.test.tsx`, `RequirePermission.test.tsx`, `client.test.ts` and `can.test.ts`.
- [x] 1.12 Team Management screens (1h). Members, grants, suspension, invitations, ownership transfer and invitation acceptance; evidence in `team.test.tsx` and `accept.test.tsx`.
- [x] 1.13 IAM and cross-tenant tests (1h). `test_iam_acceptance.py` traces AC-IAM-01..11 and AC-TEN-01..03 to their tests and fails if one is renamed away.
- [x] 2.8 Ontology Studio graph in React Flow, view and edit relationships (2.5h). Evidence in `studio.test.tsx` and `graph.test.ts`.
- [x] 2.9 Binding and authority editors (1.5h). Evidence in `bindings.test.tsx`.
- [x] 2.10 Publish confirmation showing candidate hash and validation run (1h). Evidence in `publish.test.tsx`; draft reads carry the candidate hash (`test_mappings.py`).
- [x] 3.8 SYNTHETIC/REFERENCE BRR fixture with manifest, version and checksum (1h). `packs/finops/fixtures/`, listed in the finops manifest; evidence in `test_brr_fixture.py` (checksum, parser, balances, OPEN-10 treatment per row, upload as `synthetic_reference`).
- [x] 3.9 Domain Operations screens: cases, observations, resolved values (2h). Runs with pinned versions, cases with triage and reopen, entities with observations and resolved values, identity review; case and observation reads now carry evidence provenance for the SYNTHETIC/REFERENCE label. Evidence in `operations.test.tsx`, `test_domain_observations.py`, `test_reconciliation.py` and `test_case_lifecycle.py`.
- [x] 4.7 Source Management screens, including credential status and SYNTHETIC label (1.5h). Connectors with supported and unsupported capabilities, registration without credentials, credential status (never the reference), scoped actions, webhook registration, SYNTHETIC/REFERENCE upload labels, and each webhook source's inbox with replay (`GET /api/sources/{id}/webhook-events`, `POST .../replay`). Evidence in `sources.test.tsx`, `test_webhook_inbox.py` and `test_sources.py`.
- [x] 4.8 Failure tests: invalid signature, duplicate and out-of-order events (0.5h). Evidence in `test_webhook_failures.py`: nine forgeries, unknown, unregistered and cross-tenant sources, redelivery before and after processing, arrival-order independence, a late merchant row, and retry with backoff to dead letter. Deliveries are signed with PayPal's algorithm (`tests/paypal_signing.py`); the test-only signature bypass is gone.
- [x] 5.10 Investigation screens (1.5h). `frontend/src/pages/investigations/`: start an investigation on an open case (`case.investigate` with `case.read`), re-read it while RUNNING, then the validated result (likely cause, PROPOSED classification, confidence, missing evidence, recommended playbook) with each cited evidence ID opening the trace call that returned it and its stored output. The screen creates no request. `ApiError` now keeps the envelope's other keys, so refusal `failures` reach the screen. Evidence in `investigations.test.tsx` and `client.test.ts`.
- [ ] 5.11 Playbook Requests screens (1.5h)
- [x] 5.12 Safety suite: AC-MERCH-01..07, AC-AUTHZ-06/08/15/16/17, AC-PLY-08..11 (2h). `internal-merchant-status-correct` v2 in `app/release/suites.yaml` fills all eight spec 9.7.2 categories with module-level tests the image can run. `test_safety_suites.py` checks every node and traces each criterion to its tests (the 6.5 sheet's source). Evidence in `test_worker.py`, `test_internal_merchant.py`, `test_execution_chain.py`, `test_playbook_gateway.py` and `test_release_attestation.py`.
- [ ] 5.16 PB-002 request and approval screens showing the duplicate proof (1h)
- [x] 5.17 Refund safety suite: AC-PP-08, AC-PLY-02, PB-002 parts of AC-PLY-08, AC-EXEC-MODE-03, all required capability categories (1.5h). `paypal-refunds-create` v2 in `app/release/suites.yaml`, every category filled; traced in `test_safety_suites.py`. Evidence in `test_worker_refunds.py`, `test_pb002.py` and `test_execution_mode.py`.
- [ ] 6.4 Tenant B isolation test (1h)
- [ ] 6.5 Acceptance-criteria status sheet with owners and evidence, §22.3 (0.5h)
- [ ] 6.6 Demo script with SYNTHETIC disclosure and the list of what was not built (2h)
- [ ] 6.9 Extend the demo script and rehearsal to steps 14-15 (0.5h)
- [ ] 6.7 Full team rehearsal (0.5h)
