# SettleIQ: 3-Day Delivery Plan (6 Gates, 4 People, 12 Person-Days)

## Context

SettleIQ spec v1.7 is a pre-development baseline written for a ten-day MVP. The workspace holds only the spec: no code, no repo. This plan analyzes the SDLC, splits it into the spec's six gates (G1-G6), breaks each gate into tasks, and assigns those tasks so that **all four people work in every gate**, within **3 days / 12 person-days (96 hours)**.

**The main constraint.** My rough sizing of the full P0 scope is about 47 person-days, so 12 covers roughly a quarter of it. Spec §23 says the MVP is demonstrable only when all 29 Definition of Done items are true, so this build should be called a **3-day vertical slice, not the MVP**. The plan itself changes no requirements. Spec v1.7 (2026-11-11) records approach A for PayPal: each tenant connects its own PayPal app, secret references are tenant-namespaced, and partner onboarding is deferred (OPEN-PP-02). Anything not built stays `NOT STARTED` under §22.3.

**Assumption.** I have no data on individual strengths, so the four tracks below are placeholders. Swapping names between tracks changes nothing else in the plan.

## SDLC analysis

| SDLC stage | State in the spec | Handling in 3 days |
|---|---|---|
| Requirements | Complete: 121 acceptance criteria, 29 DoD items | Frozen, no changes |
| Design | Largely done (26 frozen ADRs), but 26 OPEN items remain | Close the 9 that block the slice in the first hours; 17 stay open |
| Implementation | Spec defines G1-G6, with no frontend gate | Each gate includes its own UI work |
| Testing | Six layers in §16.7 | Built inside each gate; safety-critical tests automated |
| Release | SHADOW by default; POL-14 attestation removed in spec v1.8 | The tenant switches each exact scope to LIVE once its safety-critical tests are green |
| Demo | 17-step script in §18 | All 17 steps |
| Maintenance | P1/P2 roadmap | Out of scope |

## Scope for 3 days

**Never cut (§16.7 release blockers):** tenant isolation/RLS, decimal money, evidence preservation, permission-key authorization, owner invariant, publication hash, idempotency, post-action verification and SHADOW.

**Built thin:**
- UI covers demo-path screens only.
- The agent ships 6 of the 11 tools.
- Identity resolution uses exact IDs, with ambiguous candidates left for review.
- The Finance engine covers `STATUS_MISMATCH`, `SETTLEMENT_VARIANCE`, `DUPLICATE_CAPTURE`, and one INTRA_ENTITY and one INTER_ENTITY fixture.
- The SaaS pack is a manifest, one validator and one fixture.
- BRR uses the labelled SYNTHETIC/REFERENCE fixture.

**Not built (stays `NOT STARTED`):**
- Break-glass (AC-AUTHZ-11, DoD item 27)
- Probabilistic and relationship-based matching (AC-REC-04, AC-REC-05)
- The remaining discrepancy fixtures (AC-REC-09)
- Provider-generated BRR retrieval
- Redis and pgvector (a PostgreSQL job table replaces Redis, which §6.2 allows)
- PayPal partner onboarding (OPEN-PP-02)

**Fallback rule (Day 3, hour 7).** If the safety-critical tests are not fully green, nobody switches a scope to LIVE and the demo stops at step 10 in SHADOW, as §16.7 permits. Nobody bypasses the gate to save the demo.

PB-002 has its own exact scope and safety trace. If only its tests are not green, the refund scope stays SHADOW and the demo skips steps 14-15. PB-001 is unaffected unless a shared safety-critical test failed.

## Team tracks and load

| Person | Track | Demo identity (real mailbox) | Leads |
|---|---|---|---|
| Rishav Chowdhury | Platform and Trust | Tenant Owner | G1, G5 |
| Rahul Pandey | Integrations and Execution | Integration Admin | G4 |
| Swapnaneel Ray | Semantics, Domain and AI | Domain Operator (also holds an Approver grant for the POL-01 denial in step 9) | G2, G3 |
| Suprava Dutta | Product Experience and QA | Approver | G6 |

| Hours | G1 | G2 | G3 | G4 | G5 | G6 | Total |
|---|---|---|---|---|---|---|---|
| Rishav | 9 | 2 | 2 | 2 | 6 | 3 | **24** |
| Rahul | 4 | 3 | 3 | 11 | 6.5 | 3 | **30.5** |
| Swapnaneel | 3 | 7 | 7.5 | 1 | 8 | 4.5 | **31** |
| Suprava | 5 | 5 | 3 | 2 | 7.5 | 4.5 | **27** |
| **Total** | **21** | **17** | **15.5** | **16** | **28** | **15** | **112.5** |

**Over capacity.** PB-002 was added on 2026-11-09, taking planned work to 107.5 hours against 96 available. Its task hours are estimates. Task 5.4 is also about 8 hours over budget under the OPEN-REL-01 decision record, which makes roughly 115.5. On 2026-11-12 task 5.7 grew from 3 to 5 hours to cover all 11 registry tools, taking the table to 109.5 and the estimate to roughly 117.5. On 2026-11-12 spec v1.8 (CHG-1.8-01) removed POL-14, so task 5.4 (already spent) and tasks 6.3 and 6.8 left the plan, and so did the Tenant B isolation test (6.4); the table is now 104.5. Task 6.10 (the execution-mode switch screen, 1h) followed the same day, taking it to 105.5. Task 6.2 was dropped the same day because it only re-tested the generic pipeline: AC-OPEN-01, 02 and 06 are traced to existing tests (`test_sources.py`, `test_ingestion.py`, `test_contracts.py`). The table is now 105. Also on 2026-11-12 the demo script, status sheet and rehearsals (6.5, 6.6, 6.7, 6.9) gave way to a deployed demo business that judges run themselves (6.11 to 6.16), taking the table to 112.5. The demo business does not depend on G5 until the refund step, so it can start before Day 3. The team must add time or move work before Day 1.

## Timeline

| Gate | Runs | Exit target |
|---|---|---|
| G1 Identity and foundation | Day 1 | Day 2, hour 1 |
| G2 Semantics | Day 1-2 | Day 2, hour 2 |
| G3 Finance correctness | Day 2 | End of Day 2 |
| G4 Provider lifecycle | Day 1 (spike), Day 2-3 | Day 3, hour 2 |
| G5 Safe investigation and action | Day 2 (late), Day 3 | Day 3, hour 7 (build freeze) |
| G6 Extensibility proof and demo | Day 3 | End of Day 3 |

There is no slack. Any slip triggers the fallback rule.

## Gates and tasks

### G1: Identity and foundation (lead: Rishav, 21h)

| ID | Task | Owner | Hours |
|---|---|---|---|
| 1.1 | Create repo, Docker Compose and CI skeleton; settle stack gaps (below) | Rishav | 1.5 |
| 1.2 | Identity and tenant migrations, RLS baseline, transaction-local tenant context | Rishav | 2 |
| 1.3 | Permission catalogue as seed data, deny-by-default route guard, CI route map and role-name ban | Rishav | 2.5 |
| 1.4 | Owner pointer, atomic ownership transfer, `audit_logs` | Rishav | 1.5 |
| 1.5 | Invitation backend: hashed single-use tokens, matching-email acceptance | Rishav | 1.5 |
| 1.6 | Freeze connector manifest, capability keys and source envelope | Rahul | 1 |
| 1.7 | Source registry, `source_records`, raw evidence store with hashing | Rahul | 3 |
| 1.8 | Freeze domain-pack manifest | Swapnaneel | 0.5 |
| 1.9 | Registration, email verification, real email sending, atomic tenant creation | Swapnaneel | 2.5 |
| 1.10 | Freeze OpenAPI, adding the routes §12.2 omits | Suprava | 1 |
| 1.11 | App shell, sign-up and sign-in, tenant creation, permission-driven UI | Suprava | 2 |
| 1.12 | Team Management screens | Suprava | 1 |
| 1.13 | IAM and cross-tenant tests | Suprava | 1 |

**Exit:** IAM and tenant tests pass; every route is in the permission map; four real identities have joined; nothing is seeded.

### G2: Semantics (lead: Swapnaneel, 17h)

| ID | Task | Owner | Hours |
|---|---|---|---|
| 2.1 | Ontology composition store; platform core and Finance pack in JSON-LD | Swapnaneel | 2 |
| 2.2 | LLM draft generator from discovered metadata (draft only) | Swapnaneel | 2 |
| 2.3 | SHACL, pack validators, hash-bound validation, `confirm_publish` | Swapnaneel | 2 |
| 2.4 | Versioned mappings and authority assertions, including two sources to one property | Swapnaneel | 1 |
| 2.5 | PostgreSQL schema discovery and profiling | Rahul | 2 |
| 2.6 | CSV metadata discovery | Rahul | 1 |
| 2.7 | Atomic immutable publication; stale or concurrent attempts fail closed; audit | Rishav | 2 |
| 2.8 | Ontology Studio graph in React Flow (view and edit relationships) | Suprava | 2.5 |
| 2.9 | Binding and authority editors | Suprava | 1.5 |
| 2.10 | Publish confirmation showing candidate hash and validation run | Suprava | 1 |

**Exit:** AC-ONTO-PUB-01..05 pass; versions are immutable and pinned; the two-sources-to-one-property fixture passes.

### G3: Finance correctness (lead: Swapnaneel, 15.5h)

| ID | Task | Owner | Hours |
|---|---|---|---|
| 3.1 | Domain projection and exact-ID identity resolution; ambiguous candidates stay reviewable | Swapnaneel | 1.5 |
| 3.2 | Property observations, resolved views, explicit authority conflicts | Swapnaneel | 1.5 |
| 3.3 | Finance engine: both reconciliation scopes, settlement calculation, decimal-safe money | Swapnaneel | 2 |
| 3.4 | Version-pinned `domain_runs`, case lifecycle | Swapnaneel | 1 |
| 3.5 | Merchant PostgreSQL read and normalization | Rahul | 1 |
| 3.6 | Bank CSV and BRR parser on the shared pipeline, with provenance mode | Rahul | 2 |
| 3.7 | RLS and foreign keys on domain tables, `NUMERIC(20,6)`, cross-tenant negative tests | Rishav | 2 |
| 3.8 | SYNTHETIC/REFERENCE BRR fixture with manifest, version and checksum | Suprava | 1 |
| 3.9 | Domain Operations screens: cases, observations, resolved values | Suprava | 2 |
| 3.10 | `DUPLICATE_CAPTURE` detection and fixture: two distinct successful captures for one intended order; equal amounts alone never qualify (AC-PLY-02) | Swapnaneel | 1.5 |

**Exit:** deterministic fixtures are stable; fees already in net are not double-subtracted (AC-REC-07); ambiguous matches are never forced.

### G4: Provider lifecycle (lead: Rahul, 17.5h)

| ID | Task | Owner | Hours |
|---|---|---|---|
| 4.1 | **First task on Day 1:** Sandbox app, public HTTPS tunnel, verify provider access (OPEN-PP-01) | Rahul | 1.5 |
| 4.2 | OAuth, capture details, Transaction Search backfill, adapter manifest | Rahul | 2 |
| 4.3 | Webhook route: signature check, trusted source lookup, tenant derivation, inbox, dedupe | Rahul | 3 |
| 4.4 | Async processing, retry, replay and dead-letter state | Rahul | 1.5 |
| 4.5 | Secret references, namespaced per tenant (spec v1.7 §11.3, AC-PP-13); credential and webhook routes limited to Integration Admin | Rishav | 2 |
| 4.6 | PayPal field mappings and capture-completion authority | Swapnaneel | 1 |
| 4.7 | Source Management screens, including credential status and SYNTHETIC label | Suprava | 1.5 |
| 4.8 | Failure tests: invalid signature, duplicate and out-of-order events | Suprava | 0.5 |
| 4.9 | Sandbox duplicate-capture setup: two successful capture IDs for one intended order (§10.6, demo step 14) | Rahul | 0.5 |
| 4.10 | PayPal `refunds.create` and `refunds.read` adapter: versioned parameter contract, exact capture target, provider idempotency key, Sandbox only | Rahul | 2.5 |
| 4.11 | Write-only credential intake so a tenant connects its own PayPal app (spec v1.7 §11.3, AC-PP-14); sealed store decided for this build under OPEN-PP-03. Added 2026-11-11 | Rishav | 1.5 |

**Exit:** AC-WEBHOOK-01 and AC-PP-03/04/05/06/10/13 pass, using a real Sandbox `PAYMENT.CAPTURE.COMPLETED` event.

### G5: Safe investigation and action (lead: Rishav, 28h)

| ID | Task | Owner | Hours |
|---|---|---|---|
| 5.1 | Request, decision, approval, execution, grant and attempt tables with the full FK chain | Rishav | 2 |
| 5.2 | Two-stage policy gateway, POL-01, single-use grant issuance | Rishav | 2 |
| 5.3 | Exact-scope execution mode, SHADOW by default | Rishav | 1 |
| 5.5 | Internal-merchant adapter: conditional update, idempotency, restricted DB role | Rahul | 2.5 |
| 5.6 | Worker: atomic grant consumption, durable attempts, independent readback | Rahul | 2.5 |
| 5.7 | AI orchestrator: all 11 registry tools (spec 8.2), POL-08 permission intersection, durable tool trace | Swapnaneel | 5 |
| 5.8 | Agent response schema and prohibited-tool registry check | Swapnaneel | 1 |
| 5.9 | PB-001 definition and the request-lifecycle state machine | Swapnaneel | 1 |
| 5.10 | Investigation screens | Suprava | 1.5 |
| 5.11 | Playbook Requests screens | Suprava | 1.5 |
| 5.12 | Safety suite: AC-MERCH-01..07, AC-AUTHZ-06/08/15/17, AC-PLY-08..11 | Suprava | 2 |
| 5.13 | PB-002 definition and agent recommendation: duplicate proof, currency and amount, no prior refund or hold, always-required independent approval | Swapnaneel | 1 |
| 5.14 | Gateway and execution mode for `refunds.create`: PayPal scope independent of PB-001 (AC-EXEC-MODE-03) | Rishav | 1 |
| 5.15 | Worker refund execution: grant-bound, durable attempts, no blind retry on an ambiguous outcome, verified completion by readback | Rahul | 1.5 |
| 5.16 | PB-002 request and approval screens showing the duplicate proof | Suprava | 1 |
| 5.17 | Refund safety suite: AC-PP-08, AC-PLY-02, PB-002 parts of AC-PLY-08, AC-EXEC-MODE-03 | Suprava | 1.5 |

**Exit:** the G5 evidence list in §17 is green, including AC-PP-08 and the PB-002 parts of AC-PLY-08.

### G6: Extensibility proof and demo (lead: Suprava, 15h)

| ID | Task | Owner | Hours |
|---|---|---|---|
| 6.1 | SaaS pack: manifest, one validator, renewal/invoice fixture | Swapnaneel | 1.5 |
| 6.10 | Execution-mode switch panel: list exact scopes with mode and provider environment; Tenant Owner switches one scope at a time (demo steps 12, 14) | Swapnaneel | 1 |
| 6.11 | Demo business website (`demo-business/`, its own service and PostgreSQL, no SettleIQ code): a SaaS company selling subscription plans and one-off add-ons, with customer accounts, orders, payments, subscriptions and invoices. Its payments table is what `merchant-postgres` discovers and `merchant_setup.sql` expects: PENDING/COMPLETED status, amount, currency and order reference | Suprava | 3 |
| 6.12 | Realistic PayPal Sandbox checkout on the demo website (PayPal buttons, Orders v2, `invoice_id` set to the order reference), with two faulty procedures a judge can switch on: a lost capture confirmation (PayPal COMPLETED, shop payment left PENDING, so STATUS_MISMATCH and PB-001) and a retry that charges twice (two captures for one order, so DUPLICATE_CAPTURE and the PB-002 refund). The business's Sandbox app sends its webhooks to SettleIQ | Rahul | 3 |
| 6.13 | Source view, on the demo website only: for each order and subscription, what each source says side by side (the shop database, PayPal read live from Sandbox, subscription and invoice records), so a judge sees the discrepancy and then SettleIQ's correction or refund arrive | Suprava | 1.5 |
| 6.14 | **Reset discrepancies** button, on the demo website only: puts the shop's sources back into the problematic state (payments left PENDING, a subscription renewed without its invoice for the 6.1 pack, faulty procedures re-armed) under fresh order and payment IDs, because SettleIQ keeps every case and each action is idempotent per target. PayPal captures and refunds cannot be undone, so PayPal-side problems return through a new checkout | Swapnaneel | 2 |
| 6.15 | Deploy the demo website on its own subdomain through the prod pipeline; its database stays private to the server and reachable only by SettleIQ's merchant connectors; its Sandbox app secrets live in SSM | Rishav | 1.5 |
| 6.16 | Demo tenant created by real registration, with the business's sources connected, ontology and mappings published and both packs active; judge sign-ins for Tenant Owner, Domain Operator and Approver (POL-01 needs two people); a short judge guide on the website, including returning a scope to SHADOW with the 6.10 panel before a rerun | Rishav | 1.5 |

**Exit:** on the deployed demo website, a judge with the judge sign-ins can cause each discrepancy, watch SettleIQ resolve it under the tenant's controls, see the correction on the website and reset to run it again, with no successful mutation while a scope is SHADOW.

## Open decisions to close first

| Owner | Items | By |
|---|---|---|
| Rishav | OPEN-21-19 (no permission cache in this build). OPEN-REL-01 was closed by spec v1.6 §9.7 and is superseded by v1.8, which removed POL-14. OPEN-PP-03 is decided for this build (a sealed store under an api-only key) and stays open for production. | G1, Day 1 |
| Rahul | OPEN-01, OPEN-04, OPEN-CAP-01, OPEN-PP-01 | Day 1, first hours |
| Swapnaneel | OPEN-03, OPEN-10 (included-in-net semantics for the fixture) | OPEN-03 Day 1; OPEN-10 before 3.3 |
| Suprava | OPEN-14 (API conventions, auth transport, error envelope) | Day 1, first hours |

**Stack gaps the spec leaves open (Rishav, task 1.1).** Recommended defaults:
- Evidence storage: local volume behind a storage interface
- Secrets: Docker secrets, kept out of the repo and database
- Email: a real SMTP relay
- CI: GitHub Actions
- LLM: choose per §6.2
- Auth: a maintained library, per §11.2

## Spec issues that affect this plan

1. **Attestation timing.** Resolved by spec v1.8, which removed POL-14. Tenant A, the demo tenant, still comes from real registration (6.16).
2. **Missing routes.** §12.2 has no sign-in, suspend-member, or mapping, authority and entity read routes, though other sections require them. Task 1.10 adds them.
3. **PB-001 wording conflict.** §9.2 and §18 say "replay the event"; §4.4.1 specifies a conditional status update. This plan follows §4.4.1, the more specific contract.
4. **Break-glass has no gate.** It is required by DoD item 27 but listed in none of G1-G6. It is deferred here and needs a home in any later plan.
5. **Webhooks need a public HTTPS endpoint**, which Docker Compose alone doesn't provide. Task 4.1 covers it.
6. **Git isn't installed on this machine** and no repository exists yet. It's needed before task 1.1.
7. **The demo business departs from the spec.** §18 scripts a 17-step demo, §14.4 runs the SaaS pack in Tenant B without Financial Operations, and §22.3 expects a status for every criterion. The demo business (6.11 to 6.16) runs both packs in one tenant, and no task owns the status sheet any more. Spec v1.9 (CHG-1.9-01) is drafted for this and awaits an approver.

## Verification

- Each gate closes only when its exit evidence exists as an automated test or run artifact. A manual happy-path run doesn't count as `VERIFIED` (§22.3).
- CI must fail on a missing route permission, a role-name check in authorization code, or any safety-critical test failure.
- Final check is a judge's run of the deployed demo website, including a reset and a second run, with no successful mutation while SHADOW is active.
