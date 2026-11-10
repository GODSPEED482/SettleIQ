# SettleIQ: 3-Day Delivery Plan (6 Gates, 4 People, 12 Person-Days)

## Context

SettleIQ spec v1.6 is a pre-development baseline written for a ten-day MVP. The workspace holds only the spec: no code, no repo. This plan analyzes the SDLC, splits it into the spec's six gates (G1-G6), breaks each gate into tasks, and assigns those tasks so that **all four people work in every gate**, within **3 days / 12 person-days (96 hours)**.

**The main constraint.** My rough sizing of the full P0 scope is about 47 person-days, so 12 covers roughly a quarter of it. Spec §23 says the MVP is demonstrable only when all 29 Definition of Done items are true, so this build should be called a **3-day vertical slice, not the MVP**. No requirement changes, so no new spec revision is needed under §22.2. Anything not built stays `NOT STARTED` under §22.3.

**Assumption.** I have no data on individual strengths, so the four tracks below are placeholders. Swapping names between tracks changes nothing else in the plan.

## SDLC analysis

| SDLC stage | State in the spec | Handling in 3 days |
|---|---|---|
| Requirements | Complete: 121 acceptance criteria, 29 DoD items | Frozen, no changes |
| Design | Largely done (26 frozen ADRs), but 26 OPEN items remain | Close the 9 that block the slice in the first hours; 17 stay open |
| Implementation | Spec defines G1-G6, with no frontend gate | Each gate includes its own UI work |
| Testing | Six layers in §16.7 | Built inside each gate; safety-critical tests automated |
| Release | Signed POL-14 attestation, SHADOW by default | Build freeze and attestation on Day 3 |
| Demo | 17-step script in §18 | Steps 1-13, 16 (without refund entries) and 17 |
| Maintenance | P1/P2 roadmap | Out of scope |

## Scope for 3 days

**Never cut (§16.7 release blockers):** tenant isolation/RLS, decimal money, evidence preservation, permission-key authorization, owner invariant, publication hash, idempotency, post-action verification, SHADOW and POL-14.

**Built thin:**
- UI covers demo-path screens only.
- The agent ships 6 of the 11 tools.
- Identity resolution uses exact IDs, with ambiguous candidates left for review.
- The Finance engine covers `STATUS_MISMATCH`, `SETTLEMENT_VARIANCE`, and one INTRA_ENTITY and one INTER_ENTITY fixture.
- The SaaS pack is a manifest, one validator and one fixture.
- BRR uses the labelled SYNTHETIC/REFERENCE fixture.

**Not built (stays `NOT STARTED`):**
- PB-002 live refund (AC-PP-08, demo steps 14-15)
- Break-glass (AC-AUTHZ-11, DoD item 27)
- Probabilistic and relationship-based matching (AC-REC-04, AC-REC-05)
- The remaining discrepancy fixtures (AC-REC-09)
- Provider-generated BRR retrieval
- Redis and pgvector (a PostgreSQL job table replaces Redis, which §6.2 allows)

**Fallback rule (Day 3, hour 7).** If the safety suite is not fully green, CI issues no attestation and the demo stops at step 10 in SHADOW, as §16.7 permits. Nobody bypasses the gate to save the demo.

## Team tracks and load

| Person | Track | Demo identity (real mailbox) | Leads |
|---|---|---|---|
| Rishav Chowdhury | Platform and Trust | Tenant Owner | G1, G5 |
| Rahul Pandey | Integrations and Execution | Integration Admin | G4 |
| Swapnaneel Ray | Semantics, Domain and AI | Domain Operator (also holds an Approver grant for the POL-01 denial in step 9) | G2, G3 |
| Suprava Dutta | Product Experience and QA | Approver | G6 |

| Hours | G1 | G2 | G3 | G4 | G5 | G6 | Total |
|---|---|---|---|---|---|---|---|
| Rishav | 9 | 2 | 2 | 2 | 7 | 2 | **24** |
| Rahul | 4 | 3 | 3 | 8 | 5 | 1 | **24** |
| Swapnaneel | 3 | 7 | 6 | 1 | 5 | 2 | **24** |
| Suprava | 5 | 5 | 3 | 2 | 5 | 4 | **24** |
| **Total** | **21** | **17** | **14** | **13** | **22** | **9** | **96** |

## Timeline

| Gate | Runs | Exit target |
|---|---|---|
| G1 Identity and foundation | Day 1 | Day 2, hour 1 |
| G2 Semantics | Day 1-2 | Day 2, hour 2 |
| G3 Finance correctness | Day 2 | End of Day 2 |
| G4 Provider lifecycle | Day 1 (spike), Day 2-3 | Day 3, hour 2 |
| G5 Safe investigation and action | Day 2 (late), Day 3 | Day 3, hour 7 (build freeze) |
| G6 Extensibility proof and demo | Day 3 | End of Day 3 |

There is no slack. Any slip comes out of the final rehearsal first, then triggers the fallback rule.

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

### G3: Finance correctness (lead: Swapnaneel, 14h)

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

**Exit:** deterministic fixtures are stable; fees already in net are not double-subtracted (AC-REC-07); ambiguous matches are never forced.

### G4: Provider lifecycle (lead: Rahul, 13h)

| ID | Task | Owner | Hours |
|---|---|---|---|
| 4.1 | **First task on Day 1:** Sandbox app, public HTTPS tunnel, verify provider access (OPEN-PP-01) | Rahul | 1.5 |
| 4.2 | OAuth, capture details, Transaction Search backfill, adapter manifest | Rahul | 2 |
| 4.3 | Webhook route: signature check, trusted source lookup, tenant derivation, inbox, dedupe | Rahul | 3 |
| 4.4 | Async processing, retry, replay and dead-letter state | Rahul | 1.5 |
| 4.5 | Secret references; credential and webhook routes limited to Integration Admin | Rishav | 2 |
| 4.6 | PayPal field mappings and capture-completion authority | Swapnaneel | 1 |
| 4.7 | Source Management screens, including credential status and SYNTHETIC label | Suprava | 1.5 |
| 4.8 | Failure tests: invalid signature, duplicate and out-of-order events | Suprava | 0.5 |

**Exit:** AC-WEBHOOK-01 and AC-PP-03/04/05/06/10 pass, using a real Sandbox `PAYMENT.CAPTURE.COMPLETED` event.

### G5: Safe investigation and action (lead: Rishav, 22h)

| ID | Task | Owner | Hours |
|---|---|---|---|
| 5.1 | Request, decision, approval, execution, grant and attempt tables with the full FK chain | Rishav | 2 |
| 5.2 | Two-stage policy gateway, POL-01, single-use grant issuance | Rishav | 2 |
| 5.3 | Exact-scope execution mode, SHADOW by default | Rishav | 1 |
| 5.4 | Signed POL-14 attestation job, checked at LIVE switch and at grant issue | Rishav | 2 |
| 5.5 | Internal-merchant adapter: conditional update, idempotency, restricted DB role | Rahul | 2.5 |
| 5.6 | Worker: atomic grant consumption, durable attempts, independent readback | Rahul | 2.5 |
| 5.7 | AI orchestrator: 6 tools, POL-08 permission intersection, durable tool trace | Swapnaneel | 3 |
| 5.8 | Agent response schema and prohibited-tool registry check | Swapnaneel | 1 |
| 5.9 | PB-001 definition and the request-lifecycle state machine | Swapnaneel | 1 |
| 5.10 | Investigation screens | Suprava | 1.5 |
| 5.11 | Playbook Requests screens | Suprava | 1.5 |
| 5.12 | Safety suite: AC-MERCH-01..07, AC-AUTHZ-06/08/15/16/17, AC-PLY-08..11 | Suprava | 2 |

**Exit:** the G5 evidence list in §17 is green, except that PB-002 parts of AC-PLY-08 stay `NOT STARTED`.

### G6: Extensibility proof and demo (lead: Suprava, 9h)

| ID | Task | Owner | Hours |
|---|---|---|---|
| 6.1 | SaaS pack: manifest, one validator, renewal/invoice fixture | Swapnaneel | 1.5 |
| 6.2 | Non-financial source through the generic pipeline (AC-OPEN-01/02/06) | Rahul | 0.5 |
| 6.3 | Build freeze, create Tenant A, issue exact-scope attestation, check audit lineage | Rishav | 1.5 |
| 6.4 | Tenant B isolation test | Suprava | 1 |
| 6.5 | Acceptance-criteria status sheet with owners and evidence (§22.3) | Suprava | 0.5 |
| 6.6 | Demo script with SYNTHETIC disclosure and the list of what was not built | Suprava | 2 |
| 6.7 | Full team rehearsal | All four | 0.5 each |

**Exit:** demo runs on real identities; Tenant B cannot reach Tenant A; every criterion has an honest status.

## Open decisions to close first

| Owner | Items | By |
|---|---|---|
| Rishav | OPEN-21-19 (no permission cache in this build). OPEN-REL-01 is closed by spec v1.6 §9.7. | G1, Day 1 |
| Rahul | OPEN-01, OPEN-04, OPEN-CAP-01, OPEN-PP-01 | Day 1, first hours |
| Swapnaneel | OPEN-03, OPEN-10 (included-in-net semantics for the fixture) | OPEN-03 Day 1; OPEN-10 before 3.3 |
| Suprava | OPEN-14 (API conventions, auth transport, error envelope) | Day 1, first hours |

**Stack gaps the spec leaves open (Rishav, task 1.1).** Recommended defaults:
- Evidence storage: local volume behind a storage interface
- Secrets: Docker secrets, kept out of the repo and database
- Email: a real SMTP relay
- CI and signing: GitHub Actions with the key in CI secrets
- LLM: choose per §6.2
- Auth: a maintained library, per §11.2

## Spec issues that affect this plan

1. **Attestation timing.** POL-14 binds to the exact build and tenant, but Tenant A must come from real registration. Order on Day 3 has to be: freeze build, create tenant, then attest. Any later code change voids it.
2. **Missing routes.** §12.2 has no sign-in, suspend-member, or mapping, authority and entity read routes, though other sections require them. Task 1.10 adds them.
3. **PB-001 wording conflict.** §9.2 and §18 say "replay the event"; §4.4.1 specifies a conditional status update. This plan follows §4.4.1, the more specific contract.
4. **Break-glass has no gate.** It is required by DoD item 27 but listed in none of G1-G6. It is deferred here and needs a home in any later plan.
5. **Webhooks need a public HTTPS endpoint**, which Docker Compose alone doesn't provide. Task 4.1 covers it.
6. **Git isn't installed on this machine** and no repository exists yet. It's needed before task 1.1.

## Verification

- Each gate closes only when its exit evidence exists as an automated test or run artifact. A manual happy-path run doesn't count as `VERIFIED` (§22.3).
- CI must fail on a missing route permission, a role-name check in authorization code, or any safety-critical test failure.
- Final check is the rehearsal of demo steps 1-13, 16 and 17 on the frozen build, with no successful mutation while SHADOW is active or the attestation is missing.
