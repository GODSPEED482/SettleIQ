# SettleIQ

Multi-tenant, ontology-driven business operations platform. Financial Operations with PayPal Sandbox is the first domain.

- **Normative reference:** [`SettleIQ_Authoritative_Specification_v1.7.md`](SettleIQ_Authoritative_Specification_v1.7.md)
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
| Idempotency header | Decided for this build. `POST /api/playbooks/{id}/requests` requires an `Idempotency-Key` header of 1 to 200 printable ASCII characters, unique per tenant. A repeat with the same case, playbook and parameters returns the prior request with 200 and writes nothing; the same key for a different intent is `409 IDEMPOTENCY_KEY_REUSED`. The execution key is separate and server-derived from the playbook and its idempotency target fields, so one target is acted on at most once whatever request asks (spec 9.4) | Suprava, Rishav | OPEN-14, §9.4, AC-PLY-04 |
| Tenant selector on routes without a tenant path | Decided for this build. The `X-Tenant-Id` header. It only selects a tenant; membership proves access | Suprava | OPEN-14 |
| API versioning | Decided for this build. `/api` is version 1, stated in the OpenAPI `info.version` and in an `API-Version: 1` response header. Version 1 changes are additive only; a breaking change ships under `/api/v2` alongside it | Suprava | OPEN-14, §12.1 |
| Connector manifest, negotiation and lifecycle | Decided for this build. Fields frozen in `app/connectors/manifest.py`; unsupported capabilities are required booleans, and rate limits and retries are typed, so an unverified provider value cannot ship. Keys are canonical (`capability_registry.yaml`); a legacy spelling loads only through a registered, versioned alias. A source binds one declared protocol and source type and snapshots the capabilities; callers negotiate by key presence, because packs reference keys without versions. A source stays pinned to its registration version: minor and patch are additive, and a different major or an older manifest fails closed. There is no upgrade route: disable and re-register. Discovery runs only for a manifest with `supports_discovery`: `merchant-postgres` (the read source §4.4.1 keeps apart from the `internal-merchant` write adapter) and `tenant-csv` | Rahul | OPEN-01, OPEN-CAP-01 |
| Envelope evolution, metadata, events and files | Decided for this build. The §7.1 fields are frozen; a new field must be nullable and additive with a migration and a spec revision, and there is no envelope version field. Optional ingestion metadata, including provenance (`provider_report` versus `synthetic_reference`), lives on the raw evidence as a typed `RetrievalContext`, not in `attributes`. A record is one observation: a replay collapses, a changed payload is a new record (migration 0009). Event and file conventions are in `app/sources/conventions.py` | Rahul | OPEN-04 |
| Domain-pack manifest and activation | Fields frozen in `app/domain_packs/manifest.py`. Versions are PEP 440 ranges; a conflict declared by either pack or an unmet dependency refuses activation; namespaces are unique and IRIs use the pack namespace; rollback re-pins an earlier published version. Multi-pack overrides stay under OPEN-05 | Swapnaneel | OPEN-03 |
| Permission sensitivity and narrowable scopes | Reviewed per key in `permissions.yaml`. Permission sets stay at version 1 and re-seed in place in this build | Rishav | Spec §3.3 leaves per-key values open |
| Permission caching | Decided for this build: no cache. Every decision re-resolves from current membership and grants, so revocation applies on the next decision, including mid-run for the agent. The spec item stays open for production | Rishav | OPEN-21-19 |
| Stack gaps from task 1.1 | Raw evidence: local volume. Secrets: SSM rendered to env at deploy. Email: SMTP relay. Signing: GitHub Actions | Rishav | Delivery plan |
| Source secret references | Decided for this build. A source stores an opaque reference, never a value, set through `PUT /api/sources/{id}/credentials` (`source.credential.manage` only, AC-PP-10), which refuses a reference that does not resolve and audits create and rotate without the reference. A reference resolves only inside the tenant of the source that holds it, with the tenant taken from the trusted context and never from the reference (spec v1.7 §11.3, AC-PP-13). `managed:<NAME>` reads `<SOURCE_SECRET_DIR>/<tenant_id>/<NAME>`: `deploy/deploy.sh` renders that read-only directory on the host from SSM `/settleiq/prod/source/<tenant_id>/<NAME>`, because containers cannot reach the instance role, and skips anything outside that layout. Add one with `aws ssm put-parameter --type SecureString --name /settleiq/prod/source/<tenant_id>/<NAME>` and redeploy before registering it. `env:SOURCE_SECRET_<NAME>` reads the process environment, which every tenant shares, so it works in local development and CI only and production refuses it. Webhook ID and event types go in `config.webhook` through `PUT /api/sources/{id}/webhooks` (`source.webhook.manage`), limited to the event types the source's pinned `webhooks.receive` contract lists | Rishav | §11.3, task 4.5 |
| Connecting a tenant's PayPal account | Decided in spec v1.7 §10.1 (approach A). Each tenant creates a dedicated PayPal REST app in its own account, limited to transaction search and refunds, and its Integration Admin supplies that app's credentials; SettleIQ runs no shared platform app. The Integration Admin enters them once on the source's card (**Connect your PayPal app**), which calls the write-only intake `PUT /api/sources/{id}/credential-value` (AC-PP-14). The server checks them against PayPal Sandbox's token endpoint, seals them and points the source at `stored:<source_id>`; no response, error or audit row carries them. A platform-managed `managed:` reference stays available for operator-provisioned secrets. Partner onboarding, where no secret is shared, is deferred as OPEN-PP-02 because it needs PayPal partner approval | Rishav, Rahul | §10.1, §11.3, OPEN-PP-02, OPEN-PP-03 |
| Intake secret store | Decided for this build (OPEN-PP-03). Containers cannot write SSM, so the intake seals each credential with AES-256-GCM under `SOURCE_SECRET_KEY`, held only by the api and never by the database, and stores the ciphertext in `source_credential_values` under tenant RLS. The associated data binds it to its tenant and source, and a key fingerprint makes a changed key fail closed. Rotation overwrites; moving the source to another reference deletes it. Changing the key makes every stored credential unreadable, so tenants re-enter them. The spec item stays open for production, where a KMS-backed or managed secret store should replace the local key | Rishav | §11.3, OPEN-PP-03, AC-PP-14 |
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

Verified with Sandbox app (task 4.1): client-credentials token scopes confirmed for Transaction Search (`https://uri.paypal.com/services/reporting/search/read`) and refunds (`https://uri.paypal.com/services/payments/refund`). PayPal accepted a webhook registration for `PAYMENT.CAPTURE.COMPLETED` and refunds pointing at `/api/webhooks/paypal/{source_id}` via tunnel; that run used a placeholder source ID, and until task 4.8 the backend could not verify a genuine PayPal signature (`cryptography` was missing), so no Sandbox delivery had yet been accepted end to end. See "G4 live exit step" below. Sandbox SFTP reports require manual support provisioning, confirming reliance on the §10.3.1 SYNTHETIC/REFERENCE fixture (task 3.8). Verification script added at `scripts/verify_paypal_sandbox.py`. Duplicate capture setup recorded in `backend/packs/finops/fixtures/duplicate_capture_sandbox.py` (task 4.9).

### G4 live exit step

`backend/tests/test_g4_exit.py` proves AC-WEBHOOK-01 and AC-PP-03/04/05/06/10 with a Sandbox-shaped `PAYMENT.CAPTURE.COMPLETED` signed by PayPal's algorithm, through the public route, the processor and the run it triggers. What only a live delivery adds is PayPal's own certificate and transport. To check that, with the stack up (`docker compose up --build`, which now includes the `webhooks` processor):

1. Expose the API: `cloudflared tunnel --url http://localhost:8000` (or ngrok), giving `https://<tunnel>`.
2. In SettleIQ, as Integration Admin, register a PayPal source and connect the Sandbox app on its card (**Connect your PayPal app**, the write-only intake). A `managed:` or, locally, `env:SOURCE_SECRET_PAYPAL_SANDBOX` reference also works.
3. Register PayPal's side and note the returned webhook ID: `python scripts/verify_paypal_sandbox.py --tunnel-url https://<tunnel> --source-id <source id>`.
4. On the source's card, register that webhook ID with `PAYMENT.CAPTURE.COMPLETED`. Ingress trusts only this registration.
5. Make Sandbox emit the event: `python scripts/verify_paypal_sandbox.py --live-capture ORD-LIVE-1` (USD 1.00 of Sandbox test money by default; `--amount` changes it). If the seller account cannot take cards it prints a buyer approval link; approve it as a Sandbox buyer in a private window, then run `--capture-order <order id>`.
6. Pass: the source's **Webhook events** panel shows the event `PROCESSED` (a `400` in the tunnel log means the signature was refused, with the reason in `platform_security_ingress_logs`) and a Worker-requested domain run follows. Once the merchant's `pending` row for that capture ID is synced, the next run raises a `STATUS_MISMATCH` case expecting `COMPLETED` against it. Capture identity is the PayPal capture ID, which exists only after capture, so the merchant row comes second.

Sandbox gotchas met on the way:

- An Indian Sandbox seller cannot take the test card (`PAYEE_NOT_ENABLED_FOR_CARD_PROCESSING`) or an Indian buyer ("doesn't accept payments in your currency"). Use a United States Personal Sandbox buyer, or a United States Business Sandbox seller, which takes the card with no approval step.
- The checkout page fails without return URLs; the script sets them.
- A scope enabled on the app, such as Transaction search, only appears in a new token. PayPal reuses a token until it expires (9 hours), so revoke it with `POST /v1/oauth2/token/terminate`.

**Result, 2026-11-11 (local stack, Cloudflare quick tunnel, the team's Sandbox app connected through the write-only intake).** Order `2WU33181EW945092F` (USD 1.00, invoice `ORD-LIVE-8A56926D`) was captured as `58F06780XU2504310`. PayPal delivered `PAYMENT.CAPTURE.COMPLETED` `WH-03J471826Y773845D-3RC97953G74121235` at 18:33:35 UTC, and the route verified it against PayPal's certificate (200). It was `PROCESSED` at 18:33:36.4 (AC-PP-05), and Worker run `a5ad64e5` followed at 18:33:36.7. With the merchant's `pending` row added, run `9e6be5cf` opened a `STATUS_MISMATCH` case expecting `COMPLETED` against observed `COMPLETED` and `PENDING` (AC-PP-06), whose evidence is that event (`provider_event`) and the merchant row (`database`). The tunnel webhook was deleted from PayPal afterwards.

## Release attestation (POL-14)

Task 5.4 implements spec §9.7 in the repo. Until the steps below are done, every exact scope fails POL-14 at `SIGNATURE` or `KEY_STATUS`, so every scope stays in SHADOW. That is the intended fail-closed state.

| Piece | Where |
|---|---|
| Capability manifest, regenerated in the image and checked at startup | `backend/app/release/capability_manifest.py`, `capability-manifest.json` |
| Ten-check verifier, used by the LIVE switch and `GET /execution-readiness` | `backend/app/release/verifier.py` |
| Packaged public keys (empty) and per-capability suites (categories empty until 5.12 and 5.17) | `backend/app/release/keys.json`, `suites.yaml` |
| Running image digest, never from a header, body or environment variable | `deploy/runtime-identity/server.py` (compose service `runtime-identity`) |
| Issuing and revoking, by hand on `prod` only | `.github/workflows/attest.yml`, `.github/attest/sign.py` |
| Write-once envelope store | `aws_s3_bucket.attestations` in `infra/envs/prod/main.tf` (Object Lock, COMPLIANCE, 30 days) |

The steps below are not done yet. Rishav owns them.

1. **Environments.** In the repository settings, create `release-attestation` with Rishav as its required reviewer, and `release-publish`. Limit both to the `prod` branch. Only the `sign` job names `release-attestation`; `test_attest_workflow.py` checks this.
2. **Key.** Generate an Ed25519 seed offline and store it as the `release-attestation` environment secret `ATTESTATION_SIGNING_KEY`. Never put it in a file in the repo:
   ```
   python -c "import base64,os; print(base64.b64encode(os.urandom(32)).decode())"
   ```
   Set the environment variable `ATTESTATION_KEY_ID` to a name such as `rel-2026-11`.
3. **Public key.** Derive the public key from the seed. Add it to `backend/app/release/keys.json` as `{"key_id", "public_key" (base64), "not_before", "not_after"}` with UTC times at most 90 days apart. Then record the key on the principal:
   ```
   UPDATE platform_service_principals SET key_id = '<key id>' WHERE kind = 'release_attestor';
   ```
   A new key ships in a new image. Rotation adds the next key before the old one's `not_after`.
4. **Infrastructure.** Run `terraform apply` in `infra/envs/prod`. Set the repository variables `ATTEST_BUCKET` and `AWS_ATTEST_ROLE_ARN` from the `attest_bucket` and `attest_role_arn` outputs.
5. **Issue.** Run **release-attestation** from `prod` with `action: issue`, the tenant, `finops`, the connector, the capability and the deployed image tag (the commit SHA). The `prepare` job proves the harness only adds layers to that image and runs the capability's suite in it. The signer refuses a failed or skipped test or an empty category. The signer runs only once the reviewer approves. Each run also signs the next revocation state.
6. **Import on the host.** Copy the run's envelopes from the bucket and import them as the owner role. The state comes first, because the verifier needs a current state:
   ```
   docker compose run --rm -v "$PWD:/in:ro" migrate python -m app.release.store revocation /in/revocation.json
   docker compose run --rm -v "$PWD:/in:ro" migrate python -m app.release.store attestation /in/attestation.json
   ```
   The import refuses a state that does not chain onto the newest one, and the high-water mark never goes down.
7. **Keep it current.** An attestation lasts at most 24 hours, and so does a revocation state. Run the workflow with `action: revoke` (an empty `revoke` list is fine) and import the new state at least daily. Otherwise every LIVE scope fails `REVOCATION` at its next grant.
8. **Switch.** `PUT /api/tenants/{tenant_id}/execution-mode` with `mode: LIVE`. It re-runs the verifier and records the attestation ID. Any later code change gives a new image digest, so the attestation stops matching (`IMAGE_DIGEST`) and a new one is needed.

## Spec gaps found while scaffolding

- §3.5 gives `platform_security_approver` a "minimum support-request metadata read", but Appendix A has no key for it. The set currently holds only `platform.support.approve`.
- §12.2 omits sign-in, suspend-member, and mapping, authority and entity reads; task 1.10 added them. It also omits a connector list: `GET /api/connectors` (`connector.read`, task 4.7) returns each manifest's declared contract for the Sources screen. §10.4 requires failed processing, replay and dead-letter state to be exposed, which §12.2 has no route for: `GET /api/sources/{id}/webhook-events` (`source.read`) lists a source's deliveries with their state and no payloads, and `POST /api/sources/{id}/webhook-events/{event_id}/replay` (`source.webhook.manage`) reprocesses a FAILED or DEAD_LETTER event under its original provider event ID.
- **Backend stack settled:** the backend is FastAPI, following spec §6.2 (confirmed 2026-11-09). The Node/TypeScript API in `.claude/SettleIQ_Tech_Stack.md` is superseded. That document's frontend library choices (TanStack Router, shadcn/ui, AG Grid) still differ from the current frontend and remain open.
