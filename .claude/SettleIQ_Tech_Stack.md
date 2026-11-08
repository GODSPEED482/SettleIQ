# SettleIQ Tech Stack and Tooling

Date: 2026-11-08

## Overview

Build SettleIQ as two services and a background worker: a **Node/TypeScript API** that owns everything authoritative, and a **Python service** that runs only the model-facing parts and holds no database credentials.

Node owns auth, RLS, the policy gateway, money, the PayPal webhook path and the Worker. Python owns ontology draft generation, SHACL validation and the investigation loop. Keeping every permission check in one codebase is what makes the spec's "no bypass" rules provable.

Constraints this stack follows: Node/TypeScript backend preferred, Python allowed for the model, sponsor tools preferred but optional, Render excluded.

```
Web app (React + AG Grid) --HTTPS + session--> Node API (Express 5) <--signed webhooks-- PayPal Sandbox
                                                 |    ^
                                        invokes  |    |  tool calls
                                                 v    |
                                          Python agent (FastAPI)
                                          (no DB credentials or secrets)

Node API --single-use grant--> Worker (pg-boss) --conditional update--> Merchant DB (merchant_writer role only)
Node API --reads and writes--> SettleIQ Postgres (RLS, jobs, audit) and Evidence store (S3, SHA-256 keys, no overwrite)

The agent has no path to any data store.
```

The web app and PayPal reach the system only through the Node API. The agent calls back into Node for every tool, and only the API and Worker touch the data stores.

## Runtime stack

Every choice below is picked to make a specific spec requirement easy to prove, not just to build.

| Layer | Choice | Why it fits the spec |
| --- | --- | --- |
| Repo | pnpm workspaces + Turborepo: `api`, `worker`, `web`, `shared`, `agent` (Python) | `shared` holds the Zod schemas, permission catalogue and capability keys, so frontend and backend can't drift |
| API framework | Express 5 + Zod + `@asteasolutions/zod-to-openapi`; every route registered through one `defineRoute({ permission, schema })` wrapper | The wrapper validates input and records the permission in a route registry. The app refuses to start, and a CI test fails, if any route lacks a permission or PUBLIC/IDENTITY marker (AUTHZ-05). The same registry generates the OpenAPI spec and route map. Mount tenant, webhook and internal-Worker routers separately so their middleware can't leak across; use `express.raw()` only on the PayPal webhook route |
| Database | PostgreSQL 16+ | Required by spec |
| DB access | Kysely + `pg`, plain-SQL migrations (dbmate or node-pg-migrate) | RLS policies, the deferred owner FK and conditional-approval triggers are clearer as raw SQL. Avoid Prisma: transaction-local `SET LOCAL` tenant context fights its connection model |
| DB roles | `migrator`, `app` (no BYPASSRLS), `merchant_writer` (one SELECT + one conditional UPDATE) | The restricted merchant role is how you prove AC-MERCH-06 |
| Jobs | pg-boss | Durable, retryable, stored in Postgres; no Redis needed (spec §6.2 allows this) |
| Validation | Zod for API bodies; Ajv strict mode (`additionalProperties: false`) for manifests, action schemas and the agent response | Unknown fields are rejected, per AC-MERCH-06 and AC-AGENT-02 |
| Money | `NUMERIC(20,6)` read as string, then `decimal.js` behind a branded `Money` type | Add a lint ban on `Number()` / `parseFloat` in money code |
| Identity | Better Auth: email + password, verification, sessions | Identity only. Skip its organization/role plugin, which is role-based and conflicts with ADR-016. Memberships, grants and the owner pointer stay in your own tables |
| Email | Resend or Amazon SES | Store delivery status so failures are visible and retryable (AC-IAM-09) |
| PayPal | PayPal TypeScript Server SDK for Payments and Transaction Search; plain `fetch` for webhook signature verification | The SDK is APIMatic-generated but covers only a subset of endpoints; confirm refund and search calls on Day 1 |
| JSON-LD + publish hash | `jsonld.js` + `rdf-canonize` in Node | Canonicalize before hashing so key order can't change the hash; Node owns the hash POL-12 binds to |
| Evidence | S3 (MinIO locally), key = SHA-256 of content, never overwritten | Immutable raw evidence |
| Secrets | AWS Secrets Manager or Docker secrets; DB stores only the reference | Satisfies spec §11.3 |
| Attestation | Ed25519: GitHub Actions signs, Node verifies with `node:crypto` | Simplest workable POL-14; record key custody under OPEN-REL-01 |
| Logging | `pino` with redact paths + OpenTelemetry | Feeds Elastic (see Sponsor tools) |

## Frontend stack

The frontend is React + TypeScript on Vite, with action visibility driven only by `GET /api/me/permissions`.

| Concern | Choice |
| --- | --- |
| Build and language | React + TypeScript + Vite |
| Routing and server state | TanStack Router + TanStack Query |
| Ontology Studio graph | `@xyflow/react` (React Flow) |
| Tables (cases, observations, members, audit) | AG Grid Community |
| Components and styling | shadcn/ui + Tailwind |
| Forms | react-hook-form with the shared Zod schemas |
| API client | Generated from the frozen OpenAPI spec (APIMatic; fallback `openapi-typescript`) |

Hiding a control is never authorization: the API rechecks every call (spec §12.3).

## Python agent service

The `agent` service is FastAPI + Pydantic v2 (packaged with uv), pySHACL + rdflib, and your LLM SDK (`google-genai` if you stay with Gemini per spec §6.2).

It does three jobs:

1. Draft ontology generation from discovered metadata (draft only, never published).
2. SHACL validation of a candidate whose hash Node has already computed.
3. The investigation loop, written as a plain function-calling loop. With six tools, a framework adds little and makes clean tool traces harder.

**The boundary rule:** every agent tool is an HTTP call back into Node (`/internal/agent-tools/*`). Each call carries a short-lived delegated token binding the agent principal, the invoking user, the tenant and the case.

- Node applies the POL-08 permission intersection on every call.
- Node writes `investigation_tool_calls` and owns the prohibited-tool check.
- The agent has no DB credentials or PayPal secrets and sits only on the internal Docker network.
- Agent output returns to Node, which validates it against the Ajv response schema before persisting.

This makes AC-AI-01, AC-AI-03 and AC-AGENT-01 straightforward to prove.

## Sponsor tools

Use four sponsor tools, keep three optional, and skip three; none of them sits in a correctness-critical path.

| Tool | Verdict | Use in SettleIQ |
| --- | --- | --- |
| [APIMatic](https://www.apimatic.io) | Use | Generate the TypeScript client for `web` and the Python client for `agent` from the frozen OpenAPI spec; publish an API portal for judges. Fallback: `openapi-typescript` |
| [Postman](https://www.postman.com) | Use | Import the OpenAPI spec; four environments, one per demo identity; allow/deny collections per role; replay a captured webhook with a tampered signature; Postman CLI in CI |
| [AG Grid](https://www.ag-grid.com) | Use | Community edition for cases, observations, members and audit; cell renderers for provenance and authority badges |
| [Elastic](https://www.elastic.co) | Use, observability only | OTel traces and logs to an Elastic Cloud trial. Not a data or search store (spec §2.2, ADR-006). Don't self-host Elasticsearch on the demo box |
| [KERNEL](https://www.kernel.sh) | Optional, test harness only | Cloud browsers for Playwright, to drive the PayPal Sandbox buyer approval so the $100 payment and duplicate-capture fixtures are repeatable. Never in the product path |
| [Bryntum](https://bryntum.com) | Optional, Day 3 slack only | Task Board or a timeline for the audit lineage in demo step 16; a plain list works fine |
| [Zapier](https://zapier.com) | Optional, team notifications only | CI result or "attestation issued" to team chat. Never invitation email or anything correctness-critical (NFR-023) |
| [Astropods](https://pypi.org/project/astropods-adapter-core/) | Skip | Bridges agents to a chat messaging runtime; SettleIQ's agent is case-scoped and API-triggered, and chat would add a second authorization path |
| [Channel3](https://docs.trychannel3.com) | Skip | Product-catalog API for AI shopping agents; no fit |
| Render | Excluded | Team preference |

## Hosting and dev toolchain

Host on one AWS EC2 instance running Docker Compose behind Caddy; Caddy's automatic HTTPS on a subdomain gives PayPal the public webhook endpoint that Compose alone lacks.

| Area | Choice | Note |
| --- | --- | --- |
| Server | One EC2 instance, about 8 GB RAM | Fits Postgres, api, worker, agent and web; take volume snapshots; RDS optional |
| Reverse proxy and TLS | Caddy | Automatic HTTPS for the webhook route |
| Local webhooks | cloudflared or ngrok tunnel | For PayPal Sandbox events during development |
| Source control and CI | Git + GitHub + GitHub Actions | Install Git before task 1.1; the delivery plan notes it is missing |
| Linting | ESLint (not Biome) | Needed for a custom `no-restricted-syntax` rule banning permission-set names in authorization code (AC-AUTHZ-01) |
| Unit tests | Vitest; fast-check for settlement-math properties | |
| Integration tests | Testcontainers with a real Postgres | Mocks can't test RLS |
| Agent tests | pytest | |
| End-to-end | Playwright | Optionally on KERNEL cloud browsers |
| API role matrix | Postman CLI | |

**CI order:** lint → typecheck → unit → integration → route-map check → safety suite → build image (record the digest) → sign the attestation only if the safety suite is green.

## Day-1 checks

- [ ] Freeze the OpenAPI spec first; APIMatic, Postman and the frontend all depend on it.
- [ ] Confirm the PayPal TypeScript SDK covers capture details, refunds and Transaction Search for your Sandbox app, alongside OPEN-PP-01.
- [ ] Keep the Python service count at one; a second service costs time you don't have.

## Sources

- [Kernel provider docs (agent-browser)](https://agent-browser.dev/providers/kernel.md)
- [Channel3 API docs](https://docs.trychannel3.com/index.md)
- [astropods-adapter-core (PyPI)](https://pypi.org/project/astropods-adapter-core/0.8.0/)
- [PayPal Server SDK overview](https://openapps.pro/packages/paypal-php-server-sdk)
- [Bryntum products](https://bryntum.com/?p=19996)
- SettleIQ Authoritative Specification v1.5 and the SettleIQ 3-Day Delivery Plan