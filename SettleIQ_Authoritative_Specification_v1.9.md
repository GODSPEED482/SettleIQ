---
title: "SettleIQ"
subtitle: "Product and Technical Specification"
date: "Consolidated Authoritative Baseline | Version 1.9"
author: "SettleIQ Product and Engineering"
---

**Document type:** Software/Product Requirements Specification and Technical Design Baseline  
**Status:** Draft revision awaiting approval (§22.4.4); v1.8 stays normative until it is approved  
**Baseline date:** 12 November 2026  
**Supersedes:** SettleIQ Authoritative Specification v1.8  
**Intended audience:** Product, frontend, backend, data, AI/ML, security, QA and delivery teams

> **Authority statement.** This document is the sole normative implementation reference for SettleIQ V1. It is a complete, integrated replacement for v1.7 and all earlier baselines. Requirements, schema, policies, APIs, acceptance criteria and demonstration workflows are fully stated in this document. It is not an amendment pack and must not be implemented alongside an earlier specification. Historical versions are retained only for traceability. Items marked OPEN or P1/P2 are not V1 release requirements unless formally promoted through change control.

## Contents

- [1. Product definition](#1-product-definition)
- [2. Scope and release boundaries](#2-scope-and-release-boundaries)
- [3. Identity, tenant membership and authorization](#3-identity-tenant-membership-and-authorization)
- [4. System architecture](#4-system-architecture)
- [5. Ontology and semantic model](#5-ontology-and-semantic-model)
- [6. Canonical data and storage architecture](#6-canonical-data-and-storage-architecture)
- [7. Source normalization, entity resolution and reconciliation](#7-source-normalization-entity-resolution-and-reconciliation)
- [8. AI investigation architecture](#8-ai-investigation-architecture)
- [9. Playbook and execution specification](#9-playbook-and-execution-specification)
- [10. PayPal integration specification](#10-paypal-integration-specification)
- [11. Multi-tenant security and data protection](#11-multi-tenant-security-and-data-protection)
- [12. API surface and contracts](#12-api-surface-and-contracts)
- [13. Frontend and Business Semantic Studio](#13-frontend-and-business-semantic-studio)
- [14. End-to-end operating workflows](#14-end-to-end-operating-workflows)
- [15. Non-functional requirements](#15-non-functional-requirements)
- [16. Acceptance criteria and verification plan](#16-acceptance-criteria-and-verification-plan)
- [17. MVP implementation plan and delivery gates](#17-mvp-implementation-plan-and-delivery-gates)
- [18. Primary demonstration script](#18-primary-demonstration-script)
- [19. Risk register](#19-risk-register)
- [20. Architecture decisions frozen for V1](#20-architecture-decisions-frozen-for-v1)
- [21. Open decisions and implementation questions](#21-open-decisions-and-implementation-questions)
- [22. Traceability and change control](#22-traceability-and-change-control)
- [23. Definition of done for the MVP](#23-definition-of-done-for-the-mvp)
- Appendices: Core permission catalogue, glossary and source assumptions

## Document control

| Field | Value |
|---|---|
| Specification ID | SETTLEIQ-SRS-001 |
| Version | 1.8 |
| Status | Draft baseline for implementation planning; not evidence of implementation |
| Product | SettleIQ |
| Initial domain | Financial Operations and reconciliation |
| Initial provider adapter | PayPal Sandbox |
| MVP delivery target | Ten-day hackathon MVP, subject to the safety gates in this document |
| Source lineage | v1.0 consolidated baseline, corrected and integrated for execution lifecycle, tenant integration, webhook isolation and testability |
| Owner | Product / Engineering lead (to be assigned) |

### Normative language and priority

- **MUST / MUST NOT** denotes a binding requirement. Failure is release-blocking where the requirement protects tenant isolation, evidence integrity, authorization, money correctness, idempotency or post-action verification.
- **SHOULD / SHOULD NOT** denotes the expected approach; deviations require a recorded design decision.
- **MAY** denotes an optional capability.
- **P0** is required for the MVP demonstration; **P1** is a planned extension; **P2** is a longer-term platform capability.
- A statement that a capability is required does not imply it is already implemented. Acceptance criteria become complete only after evidence from automated or end-to-end tests.

### Design decisions carried forward into the baseline

| Historical amendment | Decision incorporated into this baseline |
|---|---|
| v0.5 | PayPal is a provider adapter, not the platform's source model. Transaction Search is for historical/backfill use, webhooks for event updates, and the Balance Reconciliation Report is a CSV/report-ingestion path in the MVP. |
| v0.6 | Real registration, email verification and admin-managed email invitations are mandatory. No pre-seeded demo users, hard-coded emails or fixed email-to-role mappings. |
| v0.7 | Authorization is permission-based. Named roles survive only as data-defined permission sets; application code checks permission keys, scopes and policy rules, never set names. |
| v0.8 | Ontology publication requires exact-hash validation and explicit human confirmation; ownership is represented by exactly one tenant owner pointer and transferred atomically; the AI agent recommends but cannot request, approve or execute a playbook. |

---

# 1. Product definition

## 1.1 Product summary

SettleIQ is a multi-tenant, ontology-driven business operations intelligence platform. It connects to a tenant's operational data ecosystem, discovers and interprets that data through a platform-core ontology, one or more composable domain ontology packs and tenant-specific extensions, then runs deterministic domain capabilities and AI-assisted investigation/workflows over the resulting context.

The first complete application is Financial Operations and reconciliation. The shared platform MUST NOT assume that every tenant is a payment business, every source is financial, or every domain record is a payment or ledger entry. SettleIQ is not a payment gateway and does not replace a tenant's systems of record. It is an integration, semantic understanding, investigation and controlled-workflow layer.

## 1.2 Core responsibility boundaries

The following responsibilities MUST remain independently testable and independently governed:

1. **Platform semantic layer:** defines cross-domain concepts, source semantics, relationships, mappings, provenance and composition of domain packs.
2. **Domain engine:** performs deterministic computations specific to an activated domain. The initial Financial Operations engine handles matching, financial invariants, settlement calculations and discrepancy detection.
3. **AI investigation orchestrator:** retrieves tenant-authorized evidence and ontology context, explains likely causes, and recommends an eligible domain playbook. It is not an execution authority.
4. **Policy and playbook engine:** validates preconditions and authorization, obtains human approval where required, issues bounded execution grants and coordinates idempotent actions.
5. **Verification and audit:** re-reads authoritative resulting state, records durable evidence and transitions, and determines whether the business outcome was actually achieved.

The ontology is not the operational system of record. Domain engines remain authoritative for deterministic domain computations. The AI agent MUST NOT replace validators, source authority policies, approval checks or execution controls.

## 1.3 Problem and intended user outcome

Operational truth is fragmented across commerce/order systems, databases, payment providers, banks, ERP/ledger systems, CRM, subscriptions, warehouses, logistics, event streams, documents and proprietary applications. Those systems can report different facts about the same logical business activity. For example, a provider can report a completed capture while a merchant payment remains pending, or the expected settlement can differ from the actual bank credit.

SettleIQ must preserve the original evidence, map source fields to explicit semantic facts, resolve which records represent the same entity, compare like-for-like observations, validate cross-entity relationships, explain detected exceptions and route any corrective action through policy-controlled workflows.

## 1.4 Product objectives

- **OBJ-01:** Connect operational sources through connector-neutral contracts, without hardcoding industry or source technology into the platform core.
- **OBJ-02:** Propose an ontology from schemas, API/event/file metadata, representative records, documentation and tenant context.
- **OBJ-03:** Let authorized business users inspect, edit, validate and explicitly publish immutable ontology compositions.
- **OBJ-04:** Preserve all source records in a generic canonical envelope and project them into typed domain models only when a domain pack requires it.
- **OBJ-05:** Run deterministic, explainable domain capabilities; Financial Operations is the first.
- **OBJ-06:** Investigate cases using tenant-scoped evidence, fact-specific source authority and traceable provenance.
- **OBJ-07:** Execute corrective actions only through versioned, policy-controlled, idempotent playbooks.
- **OBJ-08:** Enforce tenant isolation across identity, data, credentials, retrieval, processing, jobs and audit history.
- **OBJ-09:** Demonstrate an actual PayPal Sandbox path while preserving generic platform boundaries.

## 1.5 Differentiation and product promise

The proposed differentiation is the combination of business ontology generation, fact-specific source authority, cross-system entity resolution, ontology-grounded AI investigation, policy-controlled playbook execution and multi-tenant onboarding. Ontology-driven reconciliation by itself is not claimed as novel.

The intended experience is **Connect -> Discover -> Generate -> Review -> Run a domain capability**. For the Financial Operations pack, that capability is Reconcile. A connector is supported only when an installed/configured adapter implements its declared capabilities. A workflow is supported only when an activated domain pack or approved tenant configuration supplies tested semantics, validators, policies, tools and executable workflow definitions. AI MUST NOT claim unimplemented support.

## 1.6 Platform extensibility contract

These requirements apply even though the MVP implements a bounded finance use case:

- **EXT-REQ-01:** Register sources through a connector-neutral model; source categories are not a closed allowlist.
- **EXT-REQ-02:** Each connector declares its actual capabilities (discovery, metadata, batch extraction, incremental sync/CDC, events/webhooks, file ingestion, detail lookup and controlled actions). Unsupported capabilities are explicit.
- **EXT-REQ-03:** The shared source-record envelope preserves arbitrary attributes and provenance. Financial fields are not mandatory platform fields.
- **EXT-REQ-04:** Ontology composition consists of platform core, one or more versioned domain packs and tenant extensions.
- **EXT-REQ-05:** A tenant may activate multiple compatible domain packs; it is not forced into one industry label or business model.
- **EXT-REQ-06:** Domain engines, validators, entity-resolution rules, case taxonomies, agent tools, policies and playbooks are registered domain capabilities, not finance-only branches in core services.
- **EXT-REQ-07:** New connectors and domain packs can be added through documented, versioned interfaces without unrelated platform-core changes.
- **EXT-REQ-08:** MVP omissions are roadmap limitations, not permanent architectural restrictions.
- **EXT-REQ-09:** AI-generated semantic artifacts must be grounded in source evidence, validated and approved before activation.
- **EXT-REQ-10:** Industry, connector, source and domain examples are illustrative and non-exhaustive.
- **EXT-REQ-11:** Domain packs declare namespaces, dependencies, conflicts, capabilities and compatibility; explicit composition is supported.
- **EXT-REQ-12:** Unmapped records remain preserved and inspectable; they are not discarded or coerced into an unrelated domain type.
- **EXT-REQ-13:** A tenant ontology extension does not by itself create a deterministic domain engine or safe executable workflow.
- **EXT-REQ-14:** Connector transport and domain semantics are orthogonal. One source may feed several packs; a pack may consume multiple sources.
- **EXT-REQ-15:** A domain pack may represent a business function, process, product/service model, industry context or cross-cutting capability; it is not necessarily one-to-one with an industry.

---

# 2. Scope and release boundaries

## 2.1 P0 MVP commitments

| Capability | MVP commitment | Boundary / note |
|---|---|---|
| Tenant onboarding | Real registration, email verification, tenant creation and team invitations | No pre-seeded accounts or hard-coded identities |
| Source onboarding | PayPal Sandbox; merchant PostgreSQL read + internal-merchant write adapter; bank CSV; BRR CSV | Write action is separately declared; read registration grants no write authority. Generic source model remains open |
| Connector framework | Generic source registry and capability-declaring adapter interface | Implement only required adapter capabilities |
| Discovery and profiling | PostgreSQL schema discovery and CSV metadata; profile representative records | Capture nulls, cardinality, duplicate identifiers, distributions and temporal patterns |
| Ontology generation | Draft platform core + Financial Operations composition and tenant-specific proposals | Draft only; no auto-publication |
| Ontology playground | Visual editor for active ontology composition | React Flow is the proposed graph editor; avoid finance-only entity catalogue assumptions |
| Mapping and authority | Multiple versioned source bindings per semantic property; property-specific source authority | Bindings, observations and authority assertions are separate objects |
| Validation | SHACL/pySHACL plus active domain-pack validators | Finance rules run only when Financial Operations is active |
| Domain capability framework | Financial Operations engine plus small SaaS Subscription Lifecycle reference pack | SaaS pack proves extensibility; not a production SaaS operations product |
| Reconciliation | Intra-entity observation comparison and inter-entity relationship/invariant checks | Distinct scopes and traceable findings |
| AI investigation | One evidence-grounded tool-using orchestrator | Read/recommend/validate only; no request, approval or execution authority |
| Playbooks | Three predefined finance workflows | PB-003 remains non-executable until its contract is fully specified |
| Execution | Internal correction and controlled PayPal Sandbox refund | Refund requires approval; default mode is SHADOW |
| Audit and verification | Persist evidence, decisions, attempts, transitions and post-action verification | API success alone does not resolve a case |

## 2.2 P1/P2 roadmap (not a V1 commitment)

Additional native connectors across databases, SaaS, CRM, commerce, logistics, warehouses, APIs, object/file stores, event brokers and custom applications; GraphQL, CDC, SFTP, queues and streaming; additional domain packs (commerce, inventory, procurement, logistics, subscriptions and others); tenant-defined rules/workflows; advanced schema-drift and connector lifecycle management; domain-specific retrieval over manuals and policies; advanced approval/escalation policies; connector/domain-pack ecosystem; optional graph database, RDF store or dedicated search store when measured workloads justify them.

## 2.3 Explicit first-ten-day non-goals

- Replacing PayPal or another payment processor; building checkout, card or UPI authorization.
- Treating PayPal Sandbox as a production dependency.
- Shipping a complete connector, ontology pack or workflow for every possible business model.
- Allowing an LLM to publish unreviewed mappings, silently mutate active mappings, execute arbitrary SQL/endpoints, access secrets or bypass declared connector capabilities.
- Allowing an LLM to make authoritative financial calculations or override deterministic domain validators.
- Introducing Neo4j, Kafka or a separate RDF triple store before a demonstrated workload requires them.
- Dumping the entire tenant database or ontology into an unrestricted GraphRAG context.

---

# 3. Identity, tenant membership and authorization

## 3.1 Identity and tenant lifecycle

SettleIQ has global user identities and tenant-local memberships. A verified user can belong to multiple tenants and hold different permission grants in each. The same email must not create duplicate global accounts merely because the user joins another tenant. Optional industry labels are descriptive, may be multi-valued, and MUST NOT select or restrict domain-pack capability; activated domain packs determine enabled semantics.

**Registration and tenant creation:**
1. A person registers with an email address and completes email verification.
2. The verified user creates a tenant through the tenant-creation API.
3. Tenant creation, the initial ACTIVE membership and `tenants.owner_user_id` assignment MUST commit atomically.
4. The creator becomes the initial Tenant Owner. The derived owner permission set is computed from the owner pointer; it is not a normal grant.

**Admin-managed invitations:**
1. A Business Admin enters the invitee's email and selects permitted tenant permission set(s), scope(s) and applicable conditions.
2. SettleIQ creates a tenant-scoped pending invitation and sends a time-limited, single-use invitation link.
3. A new invitee registers and verifies the invited email; an existing user signs in and proves control of that same email.
4. Acceptance atomically consumes the invitation, creates/activates membership and installs the invitation grants.
5. Business Admin may list members, change permitted grants, resend/revoke pending invitations, suspend/remove members, subject to last-holder and ownership rules.

Invitation tokens MUST be high-entropy, stored hashed, expiry-bound and single-use. Resend rotates the token and invalidates prior links. Expired, revoked, used or superseded tokens fail closed. Email delivery failure is retryable and visible but MUST NOT activate membership. Invitation acceptance MUST validate token, expiry, tenant, role/permission grant policy and verified email match before creating membership. Invitation, acceptance, resend, revocation, grant changes and membership removal are audited.

**Identity requirements:** IAM-01 to IAM-11 are release criteria: verified creator becomes initial owner; Business Admin can invite a real email and assign permitted sets; only valid matching-email invites are accepted; existing users join without duplicate accounts; invalid/replayed tokens fail; unauthorized users cannot manage members; grant changes/revocation take effect on the next authorization decision; grants remain tenant-local; delivery failures do not activate users; lifecycle events are audited; no demo depends on seeded identities or hard-coded emails.

## 3.2 Tenant ownership invariant

Every tenant MUST have exactly one active human owner at all times. The `tenants.owner_user_id` column is NOT NULL and references a user who has an ACTIVE membership in the same tenant. Enforce the same-tenant membership relationship with a database constraint (for example, a composite/deferred foreign key) and transactional service validation.

The effective `tenant_owner` permission set is derived exclusively from this pointer. It MUST NOT be stored as an ordinary `member_grants` row and MUST NOT appear in invitations or generic grant payloads. A tenant owner inherits Business Admin permissions plus `tenant.delete`, `tenant.ownership.transfer` and `execution_mode.manage`. The owner does not automatically receive `playbook.request`, `playbook.approve` or execution rights. A Business Admin is not an owner unless the pointer identifies that user.

**Ownership transfer:** `POST /api/tenants/{tenant_id}/ownership-transfer` is the only operation that changes `owner_user_id`. Only the current owner may call it. The target must be a different ACTIVE member of the same tenant. Lock the tenant row, validate the target and update the pointer plus immutable audit event in one transaction. The previous owner loses owner-derived permissions immediately; any Business Admin permission must be separately and explicitly granted. Current owner cannot be removed, suspended or deactivated before transfer. Concurrent transfers must serialize and fail closed; they must never create zero or multiple owners.

## 3.3 Permission model (normative)

A **permission** is an atomic capability identified by a dotted key such as `playbook.approve`. A **permission set** is a named, versioned bundle of permissions (the data-driven replacement for a role). A **principal** is a human member, Investigation Agent, Playbook Worker or platform operator. A **grant** assigns a permission set to a principal within its applicable platform or tenant boundary, optionally narrowed to a domain pack, source or playbook, with conditions and expiry. A **policy rule** is an independent constraint evaluated after the permission check.

> **Binding rule:** authorization code MUST check permission keys and policy rules. It MUST NOT check role or permission-set names. Adding or changing a permission set is data/seed configuration, not an application-code branch.

Requirements:

- **AUTHZ-01:** Every authorization decision checks a registered permission key.
- **AUTHZ-02:** Permission sets are data; V1 system-managed sets are seed data.
- **AUTHZ-03:** Effective permissions are resolved server-side from the selected tenant's ACTIVE membership and current grants. Tokens carry identity only or short-lived revocable claims.
- **AUTHZ-04:** Grants combine by union only within the same tenant. A matching explicit DENY overrides ALLOW.
- **AUTHZ-05:** Deny by default. Every route, job and agent tool declares exactly one permission or is explicitly PUBLIC/IDENTITY. Missing declarations fail startup/CI.
- **AUTHZ-06:** Permission is necessary, not sufficient. Policy, preconditions, approval state, connector capability, execution mode and idempotency remain independent gates.
- **AUTHZ-07:** Permission metadata is enforced at grant time and decision time; principal types cannot receive incompatible permissions.
- **AUTHZ-08:** The permission catalogue is closed per release. Unknown keys fail fast. Domain packs may register namespaced keys through their manifest; collisions are rejected.
- **AUTHZ-09:** Sensitive/critical allow and deny decisions are audited with principal, permission, resource, scope and policy result.

Permission metadata includes `key`, `resource`, `action`, `scope_type`, `narrowable_scopes`, allowed `principal_types`, sensitivity (`normal`, `sensitive`, `critical`), `grant_requires`, `tenant_grantable`, registering namespace, release and description. Default tenant grant authority requires `member.role.assign`; owner-reserved grants require the dedicated owner transfer path and are never grantable by invitation/API. Platform permissions are never tenant-grantable.

Scope metadata is permission-specific, not universal. A permission declares only scopes that are semantically meaningful for that operation. Tenant administration, membership, invitation and ownership permissions are tenant-scoped and MUST NOT advertise domain-pack/source/playbook narrowing. Domain operations may be narrowed to a domain pack; source operations to a source; playbook operations to a playbook. `narrowable_scopes` may be empty. Grant resolution MUST reject a scope type not declared by the permission; the system MUST NOT invent or silently ignore a scope.

## 3.4 Policy rules

| ID | Normative rule | Enforcement |
|---|---|---|
| POL-01 | Separation of duties: the human who created a playbook request MUST NOT approve or reject that same request, even when they hold `playbook.approve`/`playbook.reject` through another permission set. | Request decision |
| POL-02 | Grant ceiling: a grantor must hold every `grant_requires` permission; each permission must be tenant-grantable; the requested set/scope/conditions must be allowed by tenant grant policy. Recheck at invitation acceptance and every grant mutation. | Invitation/grant mutation |
| POL-03 | Principal-type ceiling: reject incompatible grantees. Human-only: `playbook.approve` and `platform.support.approve`. Worker-only: `playbook.execute` and `connector.action.invoke`. | Grant and check |
| POL-04 | Ownership/last-holder protection: preserve exactly one ACTIVE tenant owner and at least one ACTIVE human holder of `member.role.assign`; reject mutations that violate either invariant. | Membership/grant/transfer |
| POL-05 | No self-elevation. A member cannot add grants to themselves. Self-removal is allowed only if POL-04 remains true. | Grant mutation |
| POL-06 | SHADOW prohibits every business-state-changing action for the scoped tenant/domain/connector action, including internal merchant-record corrections and non-financial mutations. It issues no execution grant even after approval. Only the owner-derived `execution_mode.manage` may switch an exact scope to LIVE. | Mode switch and execution-grant issue |
| POL-07 | Grants may be narrowed only by scope types declared for the permission and may carry conditions such as maximum approved amount. An out-of-scope operation has no matching grant. | Every permission check |
| POL-08 | Agent acts on behalf of the invoking human. Effective human-data permissions are the intersection of the Agent set and invoker permissions; Agent-only permissions remain. Agent write permissions are limited to append-only investigation records/traces and proposed classification metadata. | Every agent tool call |
| POL-09 | After request evaluation, any required approval and independent precondition checks, the policy gateway records a second immutable EXECUTION-stage decision with outcome ALLOW. Worker receives a single-use grant bound to tenant, execution ID, request-decision ID, execution-decision ID, playbook version, action, target and idempotency key. `approval_id` MUST be bound when the request decision says `approval_required=true`; it MUST be NULL only when that immutable request decision says `approval_required=false`. | Worker/action |
| POL-10 | Break-glass support requires reason, ticket, tenant, bounded expiry and independent approval by a different authorized platform security approver. Access is read-only by default, tightly scoped, audited to the tenant trail and auto-expiring. | Platform support |
| POL-11 | No permission bypasses preconditions, connector capability declarations, approval policy, execution mode, idempotency or independent verification. | Policy gateway |
| POL-12 | Ontology publication requires passing SHACL and every active domain-pack validator for the exact content hash, explicit human confirmation, immutable snapshot and audit. Any active-pack validator failure blocks publication. | Publication |
| POL-13 | Owner-reserved permission set is derived only from `tenants.owner_user_id`; generic membership/invitation/grant APIs cannot create it. | Membership/owner mutation |
| POL-14 | Retired in v1.8 (CHG-1.8-01). No release attestation gates execution. State-changing execution is governed by the exact-scope mode (POL-06), independent approval (POL-01), execution-stage revalidation (POL-11), the single-use grant (POL-09) and post-action verification. | None |
| POL-15 | PUBLIC provider webhook routes derive tenant context only from a trusted registered source lookup after signature verification. The payload/browser cannot supply authoritative tenant identity. | Webhook ingress |

**POL-09 request/approval/execution ordering:** the playbook request and its immutable REQUEST-stage policy decision exist before any approval. Approval, when required, references `request_id` and the REQUEST-stage decision; it never references an execution that does not yet exist. After required approval and independent action preconditions pass, the policy gateway creates a second immutable EXECUTION-stage decision with outcome `ALLOW`. An `executions` row is created only after that final decision, but before a Worker grant is issued or any side effect begins. The execution and grant reference the EXECUTION-stage decision and retain the REQUEST-stage decision for provenance. `approval_id` is nullable only when the REQUEST-stage decision records `approval_required=false`; the requester's approval policy is never supplied by the client.

**Execution-mode fail-closed semantics:** a missing, unreadable or non-LIVE exact-scope mode is SHADOW, and SHADOW is a denial at the execution stage, at grant issue and at the Worker. Read-only discovery, reconciliation and SHADOW operation may continue.

## 3.5 Default permission sets

These are V1 system-managed bundles, not code-level roles. Their names are presentation/seed metadata; authorization code checks permission keys and policies only.

| Set / principal | Permission summary | Boundary |
|---|---|---|
| `tenant_owner` (derived human) | All `business_admin` permissions plus `tenant.delete`, `tenant.ownership.transfer`, `execution_mode.manage`. | Derived only from owner pointer. No request, approve or execute by default. |
| `business_admin` (human) | Tenant read/update; member read/invite/invitation management/grant assignment/removal; role read; audit read; connector/source read/create/update/discover; domain-pack read/manage; ontology read/generate/edit/validate/publish; mapping read/edit/publish; authority/rule/policy read/edit; playbook read; domain-run read; case read; execution read. | Cannot grant owner/platform rights, switch execution mode, approve/execute or access credential values. |
| `domain_operator` (human) | Tenant/source read, source sync/file upload, source-record/raw-evidence read, domain-pack/ontology/mapping/authority/rule/policy read, domain-run read/create, entity and identity-resolution read/review, case read/triage/investigate, investigation read, playbook read/validate/request, execution read. | May reconcile, investigate and request; cannot configure credentials/webhooks, approve or execute. |
| `approver` (human) | Tenant/ontology/mapping/authority/rule/policy read, source-record/evidence/entity/identity-resolution/domain-run/case/investigation/playbook read, playbook approve/reject, execution read. | Cannot approve own request; cannot manage members/configuration or execute directly. May be narrowed by applicable playbook/amount. Full `audit.read` remains OPEN. |
| `integration_admin` (human) | Tenant/connector/source read; source create/update/delete; `source.credential.manage`; discover/sync/file upload; `source.webhook.manage`; source-record read; domain-pack/ontology/mapping read. | Required to configure PayPal credentials and webhook registration. Secret values remain in secret manager; no case, approval or execution rights. |
| `platform_operator` (platform human) | Platform tenant read/manage, connector/domain-pack register, platform ops read, support request. | No default tenant data access; cannot approve own break-glass request. Never assignable via tenant API. |
| `platform_security_approver` (platform human) | `platform.support.approve` and the minimum platform support-request metadata read required to decide requests. | Independent platform-level approval only; cannot approve own request; receives no tenant business-data access merely by approving. |
| `investigation_agent` (service) | Source/source-record/raw-evidence read, domain-pack/ontology/mapping/authority/rule/policy read, domain-run/entity/identity-resolution/case read, `case.classify`, `investigation.record`, domain compute, playbook read/recommend/validate. | May append investigation outputs/tool traces and proposed classifications only. Cannot mutate source records, business entities, case lifecycle/status, ontology/mappings/policy, requests, approvals or executions. No direct connector actions. |
| `playbook_worker` (service) | `playbook.execute`, `connector.action.invoke`, `execution.record`, `outcome.verify`, `domain.compute`, source-record/entity/case read, only within a single execution grant. | No standing grants; single-use POL-09 grant only. |

Tenant-defined custom permission sets are P1. Domain packs may contribute namespaced permissions through validated manifests; scope restrictions apply only where semantically meaningful, and a disabled pack's permissions evaluate as not granted.


---

# 4. System architecture

## 4.1 Logical components

| Component | Responsibility | Must not do |
|---|---|---|
| Multi-tenant control plane | Registration, authentication, tenant configuration, membership, permissions, source credential references | Trust a tenant ID supplied by the browser as authorization truth |
| Business Semantic Studio | Ontology draft/edit/review/publish, source mapping and authority review | Publish unreviewed model output or expose UI-hidden capabilities as security |
| Integration Manager | Register sources, configure adapters, manage sync/webhook lifecycle | Embed provider field names or finance assumptions in shared engine |
| Source adapters | Authenticate to a registered source; discover metadata; extract records/reports/files/events; verify inbound events; expose declared actions | Decide business semantics or silently mutate canonical records |
| Raw Evidence Store | Preserve original payloads, files, event messages, reports, documents, hashes and retrieval context | Replace original evidence with normalized values |
| Canonical Source Data Layer | Store tenant-scoped generic source records and domain projections; observations, entity links and resolved property views | Collapse conflicting observations into one mutable value or impose a finance-only enum |
| Platform Semantic Layer | Compose platform core, selected domain packs and tenant extensions; mappings, provenance and fact-specific authority | Become the operational system of record |
| Domain engines | Deterministic matching, invariant checks, computations, case creation | Leak domain-specific assumptions into shared ingestion/identity/tenant services |
| AI Investigation Orchestrator | Retrieve authorized evidence/context, classify likely cause and recommend eligible workflow | Execute external actions, arbitrary SQL or access another tenant |
| Policy and Playbook Engine | Validate triggers, evidence, preconditions, scope, approval, idempotency and failure path | Accept agent recommendation as policy approval |
| Verification and Audit | Re-read state; persist immutable evidence and transitions | Resolve a case merely because an API returned success |

## 4.2 Data and control flow

`User/Identity -> Tenant Authorization -> Source/Connector Manager -> Adapter -> Raw Evidence + Canonical Source Records -> Semantic Mappings/Observations/Entity Links -> Domain Engine -> Case -> AI Investigation -> Human Playbook Request -> Policy/Approval -> Execution Grant -> Worker/Connector Action -> Independent Verification -> Audit/Resolved State`.

The semantic layer and domain engines are read context for the investigation. The AI agent can recommend, but a human Operator creates the request, an authorized human Approver decides it when required, and the deterministic Worker executes only through a single-use grant. The verification stage is independent of the write path.

## 4.3 Architectural invariants

- **ARCH-01:** Platform ontology, source records, domain engines, AI reasoning and workflow execution are separate and independently testable.
- **ARCH-02:** Every domain run and workflow execution pins immutable ontology composition, mappings/source snapshots, domain-pack versions and applicable rules/playbooks.
- **ARCH-03:** All tenant-owned data/configuration/credentials references, ontology artifacts, domain records, jobs, workflow cases and evidence are tenant-scoped.
- **ARCH-04:** Authoritative domain computations use deterministic, type-safe representations. Financial calculations use decimal-safe money.
- **ARCH-05:** Every proposed/executed action traces to a case, evidence set, playbook version, policy decision, actor, approval (if required) and idempotency key.
- **ARCH-06:** Provider-specific integrations are isolated behind capability-declaring adapters; core services consume canonical contracts.
- **ARCH-07:** Multiple approved source bindings may target the same semantic property. Values remain separate provenance-bearing observations; resolved values are derived and never overwrite source evidence or ontology definitions.
- **ARCH-08:** Source-to-entity identity resolution is distinct from reconciliation. Intra-entity compares equivalent observations for one entity/property; inter-entity validates relationships/invariants across different canonical entities.
- **ARCH-09:** Every finding records scope, affected entities/properties/relationship instances, observation/evidence references and pinned mapping, authority, ontology and domain-rule versions.
- **ARCH-10:** Retired in v1.8 (CHG-1.8-01). A successful build, a registered capability or a passing pipeline never authorizes a business-state-changing operation; only the exact-scope mode, approval, revalidation and a single-use grant do.

## 4.4 Connector contract

A connector manifest MUST declare: `connector_id` and version; supported protocols/source types; authentication mode; discovery and schema/metadata capabilities; extraction modes; incremental cursor/CDC support; event/webhook support; file ingestion; detail lookup; permitted read and action operations; rate-limit and retry behavior; supported schema versions; health signals; and any relevant limits. Unsupported capabilities are explicit rather than inferred. Capability identifiers use one canonical, namespaced dotted vocabulary; adapters MUST NOT publish parallel aliases such as `batch_read` and `read.transactions` for the same operation. The V1 canonical PayPal-adapter capability keys are `transactions.read`, `captures.read`, `refunds.read`, `refunds.create`, `webhooks.receive`, `reports.balance_reconciliation.read` and `files.upload`. The V1 canonical internal-merchant adapter capability keys are `merchant.payment.read` (readback) and `merchant.payment_status.correct` (action); see §4.4.1. Connector manifests declare versions and parameter schemas for each capability. Legacy spellings are rejected by manifest validation unless a versioned compatibility alias is explicitly registered. Declaring a capability does not authorize it; a state-changing capability runs only under its exact scope's controls (§9.5, §9.7).

The platform MUST separate transport/extraction, raw evidence preservation, canonical normalization, domain projection/validation and domain processing. Connector and domain-pack contracts are orthogonal: new connectors do not change ontology semantics; new domain packs do not change connector transport. Domain-pack manifests MUST reference the canonical connector capability keys declared in §4.4 rather than invent source-specific aliases or assume capabilities a connector does not provide.

### 4.4.1 Internal-merchant write adapter (P0)

The merchant PostgreSQL read source and the internal-merchant write adapter are distinct declared capabilities, even when they share a database connection. The write adapter is not implied by registering the PostgreSQL read source and is not part of the PayPal adapter.

**Manifest identity and capability.** The adapter MUST declare `connector_id=internal-merchant`, semantic version, PostgreSQL protocol/authentication mode, tenant-bound secret reference, health signals, the readback capability `merchant.payment.read` and the exact action capability `merchant.payment_status.correct`. `merchant.payment.read` returns the current status, amount, currency and order reference of exactly one tenant-scoped payment; it is read-only and is used for precondition checks and post-action verification. Its action schema MUST accept tenant/payment identifiers, `expected_current_status` and `requested_status`. PB-001 V1 permits only `PENDING -> COMPLETED`. It MUST NOT expose arbitrary SQL, arbitrary table/column updates or generic status mutation.

**Preconditions and concurrency.** Before mutation, the adapter MUST lock the target row or perform a conditional update requiring the persisted status to equal `expected_current_status`. The tenant, merchant and payment identifiers MUST resolve to exactly one authorized row. Zero or multiple matches, stale expected state, unsupported transition or cross-tenant target MUST fail closed without mutation.

**Idempotency and verification.** The execution idempotency key MUST map to one durable action record. Reuse with a different action payload MUST be rejected; retries with the same payload MUST return the original durable outcome. The adapter MUST persist action result/evidence references. After commit, SettleIQ MUST independently re-read merchant state through `merchant.payment.read` and verify `COMPLETED` before resolving PB-001. The Worker invokes `merchant.payment.read` under `outcome.verify` within the same execution grant; the readback is not a write and cannot alter merchant state.

**Release and audit boundary.** The adapter MUST be separately registered, versioned and health-checked, and runs under its own exact scope `(tenant, Financial Operations pack/version, internal-merchant, merchant.payment_status.correct)`. Switching that scope to LIVE does not make PayPal `refunds.create` LIVE. Connector invocation is Worker-only, grant-bound and audited.

## 4.5 Domain-pack contract

A domain-pack manifest MUST declare namespace, semantic version, dependencies, compatible platform versions, ontology definitions, mapping hints, validators/invariants, entity-resolution policies, case types, agent tools and their permissions, playbook templates, connector action capabilities, conflicts and test fixtures. The platform must support loading, validating, activating, upgrading, disabling and rolling back a pack under tenant authorization. Domain packs must not collide with platform namespace or another pack's namespaced permission keys.

---

# 5. Ontology and semantic model

## 5.1 Ontology composition

The tenant ontology is composed as:

`O_tenant = O_platform_core UNION O_selected_domain_packs UNION O_tenant_extensions`

1. **Platform Core Ontology:** Tenant/Organization, Actor, DataSource, SourceObject, SourceRecord, EvidenceArtifact, DomainEntity, DomainEvent, Relationship, Policy, WorkflowCase, Action and Provenance.
2. **Domain Ontology Pack(s):** independently versioned domain semantics. Financial Operations is the initial pack; examples of future packs include commerce, SaaS/subscriptions, logistics, inventory, procurement and marketplace operations.
3. **Tenant Extension Ontology:** reviewed tenant-specific classes, properties, relationships, mappings, rules and vocabulary.

A tenant may activate multiple compatible packs. Tenant extensions preserve inherited meaning by default; overrides require an explicit versioned migration, validation and approval.

## 5.2 Financial Operations starter classes

The following are Finance/domain types, not platform-wide mandatory classes:

| Class | Meaning |
|---|---|
| Business | Platform Core tenant/business workspace |
| Customer | Customer associated with business activity |
| Order | Commercial order |
| Payment | Payment initiated for a commercial transaction |
| Authorization | Authorization associated with a payment |
| Capture | Captured payment amount/event |
| Refund | Reversal of a captured amount |
| SettlementBatch | Group/batch of financial settlement entries |
| SettlementEntry | Individual component of a settlement |
| BankTransaction | Actual bank credit/debit record |
| ProcessingFee | Provider fee |
| Chargeback | Disputed transaction adjustment |
| AccountingEntry | Accounting ledger entry |
| PaymentProvider | Provider adapter/source |
| DataSource | Platform Core registered database/API/file/event/warehouse/other source |
| ReconciliationCase | Platform Core generic case specialized by finance pack |
| Playbook | Platform Core versioned workflow definition; domain packs register templates |

**Finance cardinality rule:** SettlementBatch MUST be distinct from SettlementEntry. One bank credit may correspond to many captures, fees, refunds and adjustments. This is a finance-specific rule, not a universal platform assumption.

## 5.3 Relationship examples

| Relationship | Domain | Range |
|---|---|---|
| `placesOrder` | Customer | Order |
| `hasPayment` | Order | Payment |
| `hasAuthorization` | Payment | Authorization |
| `generatesCapture` | Payment | Capture |
| `refundsCapture` | Refund | Capture |
| `includedInSettlement` | FinancialEntry | SettlementBatch |
| `settledThrough` | SettlementBatch | BankTransaction |
| `chargedBy` | FinancialEntry | PaymentProvider |
| `recordedIn` | FinancialEntry | DataSource |
| `hasDiscrepancy` | FinancialEntity | ReconciliationCase |
| `resolvedUsing` | ReconciliationCase | Playbook |

Domain-pack relationship definitions carry domain/range, cardinality, constraints, provenance and lifecycle/version metadata. Runtime relationship instances are stored separately from ontology definitions.

## 5.4 Source mappings and comparability

A source mapping binds an external object/field to a semantic class/property. One source object may map to several domain concepts and one semantic concept may be supported by many source objects. Supported source shapes include relational tables/columns, API fields/JSON paths, GraphQL response paths, file columns, event/message fields and derived values.

Each binding includes source ID, external object/resource, field/path, semantic class/property IRI, datatype, transform, nullability, uniqueness, synchronization strategy, evidence/method and mapping version. Bindings are explicit, reviewable, tenant/domain-scoped and versioned. AI may propose them but cannot activate or mutate an active mapping.

Multiple bindings may target one semantic property, but only if their business meaning, units/currency, normalization and temporal context are comparable. Example: Shopify `orders.total_price` and ERP `sales_orders.order_total` may both bind to `commerce:totalAmount` after equivalent normalization. A warehouse fulfilled-value field MUST bind to a distinct property such as `commerce:fulfilledAmount`, not `commerce:totalAmount`. Similar datatype or currency alone does not establish semantic equivalence.

## 5.5 Property observations and resolved views

For each mapped source record, persist a property observation containing at minimum:

- tenant and canonical entity reference;
- semantic class/property IRI;
- source record, source/object/field reference;
- raw and normalized values;
- datatype, unit and currency where applicable;
- valid/event time and ingestion/observed time;
- raw-evidence ID/hash;
- transformation and mapping versions.

Observations SHOULD be append-only. A correction or later source report creates a new observation rather than rewriting history. A resolved property view is a derived, versioned projection of eligible observations and authority policy. It retains contributing observation IDs and reports `RESOLVED`, `CONFLICT`, `UNRESOLVED`, `STALE` or `HUMAN_REVIEW`. No value is selected by ingestion order or arbitrary source rank when there is no unique applicable authority.

## 5.6 Fact-specific source authority

Authority assertions are scoped by semantic fact/property, entity/domain, source, validity interval and verification method. A source may be authoritative for one property and not another. Authority resolution supports unknown, disputed, overlapping and expired authority. It selects/prioritizes a value for the resolved view; it never deletes or suppresses other observations.

Examples for Financial Operations: PayPal capture record may be authoritative for capture completion; the bank transaction for actual bank credit; merchant order database for internal order status; ERP ledger for accounting posting. These are examples, not global rules. If authority assertions overlap, conflict, expire or are missing, the resolved state is `AUTHORITY_CONFLICT` or `UNRESOLVED` and follows the configured review path. Recency, connector reliability or an LLM confidence score cannot silently override explicit authority policy.

## 5.7 Ontology generation, validation and publication

Inputs may include database schemas/metadata, API specifications, GraphQL/event schemas, connector metadata, sample records/files, data dictionaries, process documents and tenant business context.

Generation stages: source discovery; metadata profiling; candidate entity/relationship inference; draft composition; structural and domain validation; human review; explicit confirmation; immutable publication. Structural evidence (foreign keys, API references, event correlation IDs) is evidence, not automatic proof of business semantics. UI MUST distinguish observed facts from inferred proposals. Confidence labels are heuristic review priorities unless calibrated and empirically evaluated.

**Publication gate (POL-12, binding):**
1. Caller is an authenticated human with `ontology.publish` for the target tenant and ontology scope.
2. A candidate snapshot is hashed. SHACL and every active domain-pack validator pass for that exact content hash.
3. Any edit after validation invalidates the prior validation for publication.
4. The authenticated human submits explicit `confirm_publish=true` for that exact candidate.
5. Server atomically creates an immutable published snapshot/version and the audit event; stale/concurrent publication fails closed and cannot overwrite a prior version.
6. Audit records tenant, ontology/composition, candidate and published versions, content hash, validation run, publisher, confirmation timestamp and outcome.

V1 does not require a second-person approval for ontology publication. A future two-person gate would require a separate `ontology.approve` permission and separation-of-duties rule. Every domain run/workflow remains pinned to the immutable version it started with.

## 5.8 Semantic exchange and validation technology

JSON-LD is the selected ontology interchange representation; PostgreSQL JSONB is the initial registry storage; SHACL/pySHACL is the proposed graph-constraint validation mechanism. A proper JSON-LD context and namespaces are required; illustrative JSON fragments are not a complete OWL ontology. SHACL validates graph structure; domain-pack validators check domain semantics and operational correctness. Example finance constraints: Payment has an amount; each monetary amount has a currency; Capture has an associated Payment; SettlementEntry belongs to a SettlementBatch. Finance rules apply only when Financial Operations is active.

---

# 6. Canonical data and storage architecture

## 6.1 Data categories and initial storage

| Category | Contents | Initial storage |
|---|---|---|
| Semantic data | Platform/domain/tenant ontology, mappings, authority, pack manifests | PostgreSQL/JSONB; JSON-LD interchange |
| Source/operational data | Generic source records, events, canonical entities, domain projections | PostgreSQL canonical source tables + domain tables/views |
| Reasoning/workflow | Cases, investigation outputs, playbook runs, approvals | PostgreSQL or repository behind platform interfaces |
| Raw evidence | Original payloads, extracts, files, webhooks, reports, documents | Object storage + append-only evidence records |
| Optional retrieval | Tenant docs and unstructured context | pgvector, only if required |
| Optional jobs/cache | Durable/background job coordination and short-lived cache | Redis + RQ/Celery if required |

Do not force all data into a graph database or domain-specific schema. PostgreSQL is the initial system of record for tenant metadata, source records, ontology registry, domain projections, cases and audit. Specialized storage may be added behind repository interfaces only after measured need.

## 6.2 Recommended technology baseline

| Component | Baseline | Purpose |
|---|---|---|
| Frontend | React + TypeScript | Multi-tenant UI and semantic studio |
| Graph editor | React Flow | Entity/relationship editor |
| Backend | FastAPI | REST API, orchestration and data processing |
| Database | PostgreSQL | Tenant data, source records, ontology, domain projections, cases, audit |
| Flexible definitions | PostgreSQL JSONB | Ontology definitions, playbooks and configuration |
| Semantic exchange | JSON-LD | Ontology interchange |
| Validation | SHACL/pySHACL | Ontology and graph constraints |
| AI | Gemini or another available LLM | Draft generation and evidence-grounded investigation |
| Retrieval | SQL; optional pgvector | Structured retrieval/document context |
| Jobs | Redis + RQ/Celery if required | Background ingestion and domain processing |
| Workflow | Explicit Python state machine | Durable playbook execution |
| Deployment | Docker Compose | Reproducible MVP deployment |
| Initial provider | PayPal REST APIs + webhooks | Finance adapter; not the platform's only source type |

For the ten-day build, avoid premature Neo4j, Kafka and a separate RDF triple store. The platform should not use infrastructure complexity as a substitute for proving semantic and reconciliation correctness.

## 6.3 Logical relational schema

The following is the V1 logical relational baseline. Every table listed as tenant-owned MUST carry a non-null `tenant_id`, appropriate foreign keys/check constraints, indexes, RLS policy and transaction boundaries. The schema separates a request, its policy decision, any required human approval, the execution created after authorization, and each side-effect attempt. These are separate durable records, not a combined `playbooks / executions` aggregate.

| Table / aggregate | Required fields and responsibility |
|---|---|
| `users` | Global identity, normalized/verified email, authentication metadata, status. |
| `tenants` | `id`, name, business metadata, optional `industry_labels` array (descriptive only; never a single capability selector), `owner_user_id NOT NULL`, created/updated timestamps. |
| `tenant_members` | `tenant_id`, `user_id`, membership status, joined/created timestamps; unique `(tenant_id,user_id)`. |
| `tenant_invitations` | Tenant, normalized email, token hash, status, expiry, inviter, created/accepted/revoked timestamps. |
| `tenant_invitation_grants` | Invitation plus permission set, permitted scope, conditions; owner-reserved and platform permissions prohibited. |
| `permissions` | Key, resource/action, applicable scope type, narrowable scopes (possibly empty), principal types, sensitivity, grant requirements, grantability, namespace/release. |
| `permission_sets` / `permission_set_items` | System/tenant scope, key/display name, principal type, version/status; each permission item is ALLOW or DENY. |
| `member_grants` | Tenant/principal/set, semantically valid scope, conditions, grantor, expiry and revoke metadata. |
| `service_principals` | Tenant Agent/Worker IDs, kind and status. Platform support principals use a separate registry. |
| `data_sources` / `source_objects` | Tenant/source identity, connector/protocol, config/secret reference, declared capabilities, status; object/schema snapshots and discovery time. |
| `raw_evidence` | Tenant/source, immutable original bytes/payload, hash, event/report IDs, retrieval and provider/source timestamps. |
| `webhook_inbox` | Tenant/source, provider event ID/type, evidence reference, receipt/provider timestamps, signature-verification result, processing state/attempts; unique `(tenant_id,source_id,provider_event_id)`. |
| `source_records` | Tenant/source/object/external ID, namespaced record type, arbitrary JSONB attributes, raw evidence, event/observation times, payload hash, connector/mapping version. |
| `domain_packs` / `tenant_domain_packs` | Pack namespace/version/manifest/dependencies/capabilities and tenant activation/configuration. |
| `ontologies` / `ontology_relationships` | Immutable ontology composition versions, classes/properties/relationship definitions and content hashes. |
| `source_mappings` | Tenant/ontology/source/object, semantic class/property, mapping definition/version/status. |
| `domain_entities` / `entity_source_links` | Tenant, ontology, type IRI, canonical identity/projection; source-record links, identity decision, status and provenance. |
| `entity_resolution_decisions` | Candidate/selected entities, status, method/features/evidence, policy version, reviewer/time. |
| `property_observations` | Entity/property/source/field, raw/normalized value, type/unit/currency, valid/event/observed time, evidence and mapping version. |
| `resolved_property_views` | Derived tenant/entity/property/value/status, authority and policy/mapping versions, contributing observation IDs and resolved time. |
| `domain_relationship_instances` | Runtime subject/predicate/object, tenant/ontology, provenance and observation time. |
| `domain_runs` | Tenant, domain-pack/version, ontology composition version, source snapshots, mapping/rule versions and state/timestamps. |
| `domain_cases` | Tenant, case type, reconciliation scope, severity, lifecycle status, affected entity/property/relationship IDs, observations, evidence and version references. |
| `investigations` | Tenant, case, initiator and invoking human, Agent principal, status, model/prompt/tool-registry versions, input evidence references, structured result JSON, recommendation, confidence and timestamps. |
| `investigation_tool_calls` | Tenant, investigation, tool name/version, required permission, scoped input/output hashes or redacted payloads, evidence references, outcome/error and timestamps. Append-only. |
| `playbooks` | Tenant/domain scope, immutable definition/version/status, trigger, required evidence, preconditions, approval policy, action capabilities, idempotency and verification contract. |
| `playbook_requests` | `id`, `tenant_id`, case, playbook ID/version, requester principal/user ID, request input/evidence references, status, request idempotency key and timestamps. Created by a human before any approval or execution. |
| `policy_decisions` | `id`, `tenant_id`, `request_id`, nullable `parent_decision_id`, evaluation stage (`REQUEST` or `EXECUTION`), policy version, `approval_required` boolean, decision with stage-specific values: REQUEST = `DENY`, `NEEDS_APPROVAL`, `ALLOW_PENDING_PRECONDITIONS` or `POLICY_BLOCKED`; EXECUTION = `DENY`, `ALLOW` or `POLICY_BLOCKED`, evaluated conditions/preconditions, reason and timestamp. Immutable; every request has a REQUEST decision and every execution has a separate EXECUTION decision. |
| `playbook_approvals` | `id`, `tenant_id`, `request_id`, `request_policy_decision_id`, approver user ID, decision, reason, policy version, scope/conditions and timestamp. It references the request-stage decision, never an execution or execution-stage decision. Unique decision slot per request/policy version unless explicit multi-stage policy is configured. |
| `executions` | `id`, `tenant_id`, `request_id`, `request_policy_decision_id`, `execution_policy_decision_id`, nullable `approval_id`, playbook version, status, idempotency key, worker principal, start/completion timestamps and result reference. Created only after required approval, preconditions and an EXECUTION-stage ALLOW decision, before grant issuance/side effects. |
| `execution_action_attempts` | `id`, `tenant_id`, `execution_id`, sequence, connector/action/target, idempotency key, request/response evidence references, status, error/timeout classification and timestamps. Append-only. |
| `execution_grants` | `id`, `tenant_id`, `execution_id`, `request_policy_decision_id`, `execution_policy_decision_id`, nullable `approval_id`, action, target, idempotency key, permission keys, issued/expiry/consumed timestamps and token hash only. |
| `execution_mode_settings` | `id`, `tenant_id`, `domain_pack_id`/version, `connector_id`, canonical `action_capability`, mode (`SHADOW`/`LIVE`), `changed_by`, `changed_at` and audit reference. Unique exact scope key `(tenant_id, domain_pack_id, connector_id, action_capability)`; absent rows resolve to SHADOW. No wildcard, parent-scope or tenant-wide LIVE inheritance is permitted in V1. |
| `platform_support_requests` / `platform_support_approvals` | Tenant, requester platform principal, reason/ticket/scope/expiry, independent approver, decision and audit references; requester and approver must differ. |
| `audit_logs` | Tenant, principal/invoker, permission/resource/decision, policy rules, action/entity/evidence and timestamp. |

**Lifecycle and referential invariants:**

1. `playbook_requests` is created first. `policy_decisions.request_id` references it. The policy decision records whether approval is required and why.
2. The REQUEST-stage policy decision records whether approval is required and the evaluated policy version. When approval is required, `playbook_approvals` references the request and this REQUEST-stage decision. POL-01 compares the human `requested_by` with `approver_user_id`; it does not depend on an execution row.
3. When approval is not required, no approval row is created. The immutable REQUEST-stage decision's `approval_required=false` is the only basis for a NULL `approval_id`.
4. After approval (if required) and independent precondition checks pass, create a second immutable EXECUTION-stage `policy_decisions` row linked through `parent_decision_id`; it must have outcome `ALLOW`. Do not mutate the REQUEST-stage decision.
5. `executions` is created only after the EXECUTION-stage ALLOW decision. It has its own primary key and non-null `tenant_id`, and references both policy decisions plus `approval_id` when required.
6. `execution_grants` references both decision records and the execution. It references an approval if and only if the REQUEST-stage decision says approval was required and the approval is approved. Enforce the relationship with application validation and database constraints/triggers where supported.
7. The grant's consumption and the execution's transition to `EXECUTING` commit in one transaction. A grant is consumed exactly once and its `execution_policy_decision_id` must reference an immutable EXECUTION-stage `ALLOW` decision whose parent chain includes the request-stage decision associated with the approval.
8. Every investigation, request, decision, approval, execution, action attempt, and support request/approval used for a tenant action is tenant-scoped or contains an explicit tenant foreign key protected by RLS.

**Additional data constraints:**

- Every tenant-owned row MUST carry `tenant_id` and be protected by server-derived tenant context and RLS.
- Every domain run and execution pins ontology composition, selected pack versions, source/schema snapshots, mapping versions, rules and playbook versions.
- Money uses `NUMERIC(20,6)` or a documented integer-minor-unit strategy; application floats MUST NOT be authoritative.
- Raw source payloads are preserved and never overwritten by normalized data.
- Provenance includes source, event/report identifier, retrieval timestamp, source timestamp, payload hash, transformation version and mapping version.
- No correctness-critical request, decision, approval, execution, webhook, tool trace or action attempt may exist only in process memory or an ephemeral queue.

---

# 7. Source normalization, entity resolution and reconciliation

## 7.1 Generic canonical source-record envelope

All adapters normalize into one domain-neutral envelope before domain projection. At minimum the logical fields are:

```json
{
  "id": "uuid",
  "tenant_id": "uuid",
  "source_id": "uuid",
  "source_object_id": "uuid",
  "external_id": "string-or-null",
  "record_type": "namespaced-type-iri",
  "attributes": {},
  "raw_evidence_id": "uuid",
  "occurred_at": "timestamp-or-null",
  "observed_at": "timestamp",
  "payload_hash": "sha256",
  "connector_version": "semver",
  "mapping_version": "version-or-null"
}
```

`attributes` is an arbitrary structured payload; the envelope does not require financial fields. The platform preserves unknown attributes and unmapped records. Domain packs project only records they recognize and validate. A source record is not itself a canonical domain entity.

## 7.2 Source-to-entity identity resolution

Identity resolution determines which canonical `DomainEntity` a source record represents. It is distinct from field mapping and reconciliation. Multiple source records may link to one canonical entity; a source record may participate in more than one domain projection/entity only where domain semantics allow it.

Candidate matching signals may include exact external IDs, stable identifiers, foreign-key/API references, event correlation IDs, relationship context and probabilistic features. The engine records candidate entities, selected entity (if any), method/features, evidence, policy version and reviewer/decision. Ambiguous candidates remain unresolved and reviewable; they are never force-attached. Blocking is expected to reduce candidate comparisons, but thresholds/features and acceptable false-link rates remain an implementation decision to be measured. Source records and observations remain preserved regardless of a link decision.

## 7.3 Reconciliation scopes

**INTRA_ENTITY** reconciliation compares semantically comparable observations for the same canonical entity/property. A finding references the property, each contributing observation and resolution/authority state. It does not mutate the ontology or raw evidence.

**INTER_ENTITY** reconciliation validates relationships or invariants across distinct canonical entities. Examples include Order-to-Invoice or Order-to-Shipment consistency, Payment-to-Capture, SettlementBatch-to-BankTransaction and finance-specific settlement invariants.

Identity resolution answers “which entity does this record represent?” Reconciliation answers “do comparable facts about that entity agree?” or “do related entities satisfy the required invariant?” These are separate stages and separate case findings.

## 7.4 Financial Operations matching and calculations

The Finance engine performs deterministic transaction matching, aggregation and invariant validation. Candidate signals may include provider transaction/capture IDs, merchant order IDs, amounts, currencies, references, timestamps, statuses and relationship links. Exact identifiers and domain constraints take precedence over similarity alone. Matching thresholds must be configurable/versioned and empirically evaluated; an LLM score is not a substitute for a deterministic match decision.

A settlement calculation must distinguish gross captures, refunds, processing fees, chargebacks, withdrawals and other adjustments. A common reconciliation expression is:

`Expected Net = Captures - Refunds - Fees - Chargebacks + Adjustments`

`Variance = Expected Net - Bank Credit`

The exact signs, included/excluded entry types, provider accounting treatment and settlement grouping are pack-specific. The P0 Balance Reconciliation Report CSV is the required MVP source for the settlement fixture. Its included-in-net flags and accounting semantics MUST be mapped and tested before settlement action is enabled. The engine MUST NOT double-deduct a fee already netted by the provider report. All calculations use decimal-safe money, explicit currency and documented rounding. Cross-currency records must not be compared or aggregated without an explicit currency/FX rule.

## 7.5 Finance case taxonomy and required fixtures

Initial case types include `TXN_MISSING`, `STATUS_MISMATCH`, `AMOUNT_MISMATCH`, `DUPLICATE_CAPTURE`, `REFUND_MISMATCH`, `FEE_MISMATCH`, `SETTLEMENT_VARIANCE`, `UNMATCHED_BANK_CREDIT` and `LEDGER_MISMATCH`. Domain pack owns codes and severity rules; they are not platform-wide enums.

At minimum test: missing transaction; provider COMPLETED / merchant PENDING; amount mismatch; genuine duplicate capture; refund mismatch; fee mismatch; bank settlement variance; unmatched bank credit; ledger mismatch; two source fields mapping to one property; semantically different warehouse fulfilled amount; inter-entity order/invoice or order/shipment invariant; overlapping/unknown source authority; cross-currency mismatch; ambiguous entity identity; duplicate/out-of-order event; and a source record that remains unknown but preserved.

## 7.6 Required reconciliation acceptance criteria

- A record is preserved in the generic envelope even if no active pack recognizes it.
- The same logical entity can be represented by records from PayPal, PostgreSQL and bank CSV through explicit identity links and recorded decisions.
- Many-to-one bindings create separate mapping entries and property observations, not duplicate ontology properties or an overwritten source value.
- A resolved property view retains non-authoritative observations and exposes conflicts/unresolved state.
- A fulfilled-value property is not falsely compared to original order total.
- INTRA_ENTITY and INTER_ENTITY findings are distinct, correctly scoped and trace their exact evidence and version snapshots.
- Ambiguous identity links remain unresolved until reviewed.
- Deterministic Finance fixtures produce stable outcomes for identical version-pinned inputs.

---

# 8. AI investigation architecture

## 8.1 Agent responsibilities and limits

Use one tool-using investigation orchestrator for the MVP. It retrieves authorized ontology/evidence context, classifies likely cause, summarizes supporting/contradicting evidence, recommends a domain playbook and identifies missing evidence or required approval. The Agent does not determine authoritative source policy, calculate money, publish ontology, create a playbook request, approve, reject or execute any action.

The AI runs under a registered service principal. Its effective human-data permissions are intersected with the invoking member's permissions under POL-08. Tenant, domain pack, source, case and evidence scope are passed explicitly to tools and checked server-side. Tools must not expose secrets or unrestricted query execution.

The Agent MAY perform only these narrowly defined writes:

- append an immutable `investigations` result containing its structured analysis, evidence references, model/prompt/tool versions and recommended playbook;
- append `investigation_tool_calls` trace rows with redacted inputs/outputs, permission checks, evidence references, outcome and timestamps;
- persist a proposed case classification as investigation metadata, explicitly marked `PROPOSED`, without mutating the authoritative case lifecycle/status or business records.

These are investigation/audit writes, not business-state or workflow writes. The Agent MUST NOT mutate source records, canonical business entities, resolved business properties, case state/assignee, ontology, mappings, authority, rules, policies, playbook definitions, membership/grants, playbook requests, approvals, executions or connector/provider state. The permission keys `investigation.record` and `case.classify` authorize only the bounded append/proposal operations described here. All other writes remain forbidden.

Every tool call is logged with principal, invoker, permission, tenant/scope, redacted inputs/outputs, evidence references and model/config version. Agent output is untrusted data and is revalidated by deterministic services.

## 8.2 Investigation tool registry

The V1 tool inventory is limited to read, classify, compute and recommend/validate operations, such as:

| Tool | Required permission / boundary |
|---|---|
| `get_ontology_context` | `ontology.read`; active/pinned tenant composition only |
| `get_source_authority` | `authority.read`; exact fact/entity scope |
| `get_source_records` | `source_record.read`; tenant and invoking member scope |
| `trace_domain_relationships` | `entity.read`; registered domain relationships only |
| `calculate_settlement_variance` | `domain.compute`; deterministic Finance function, not LLM arithmetic |
| `get_case_evidence` | `case.read`; raw payload excerpts additionally require `evidence.raw.read` |
| `get_property_observations` | `entity.read`; preserve provenance and value/time metadata |
| `get_entity_resolution_trace` | `entity_resolution.read`; candidate/evidence trace |
| `classify_domain_case` | `case.classify`; taxonomy registered by active pack |
| `recommend_playbook` | `playbook.recommend`; eligible templates only |
| `validate_playbook` | `playbook.validate`; dry-run with no side effects |

The Agent registry MUST NOT contain `request_approval`, `create_playbook_request`, `approve`, `reject` or `execute` tools. Tool registry validation MUST fail if a prohibited action tool appears.

## 8.3 Agent response contract

The agent returns a structured recommendation, for example:

```json
{
  "case_id": "uuid",
  "classification": "DUPLICATE_CAPTURE",
  "root_cause_summary": "Two distinct completed captures appear linked to one order.",
  "evidence": [
    {"evidence_id": "uuid", "source_id": "uuid", "record_id": "uuid", "fact": "capture status"}
  ],
  "recommended_playbook": "finops:refund_duplicate_capture",
  "requires_approval": true,
  "confidence": "high",
  "missing_evidence": []
}
```

The example is illustrative. The production schema must validate all fields and references. `evidence` must contain tenant-scoped references and concise provenance metadata, not secrets or unrestricted raw payload dumps. The output MUST NOT contain `request_id`, `approval_id` or `execution_id`; those records are created by human/workflow services. Confidence labels are qualitative unless calibrated.

## 8.4 Human request and decision authority

1. The Agent recommends only; it creates no workflow records.
2. A human Operator with `playbook.request` persists the request.
3. The policy gateway persists the immutable REQUEST-stage decision.
4. If approval is required, an independent human Approver (`playbook.approve` / `playbook.reject`, subject to POL-01/POL-07) persists an approval linked to the request and the REQUEST-stage decision; otherwise no approval row is created.
5. The gateway revalidates preconditions, target, source authority, connector capability and exact-scope execution mode.
6. The gateway persists a separate EXECUTION-stage decision; only `ALLOW` proceeds.
7. The execution row is persisted, referencing the request and both decision IDs.
8. After commit, the gateway issues a single-use grant bound to the execution, both decisions, the conditional approval, the exact action/target and the idempotency key. The Worker consumes it atomically, executes and verifies authoritative state. AI cannot impersonate any of these principals.

Creating a playbook request is a human operation, not an agent tool. The frontend may expose “Create playbook request” on the Operator's case review view; the backend authorizes it independently.

## 8.5 AI quality and safety requirements

- Ground each conclusion in explicit source/evidence IDs and active ontology/domain versions.
- Distinguish observed fact, inferred hypothesis and missing evidence.
- Do not invent source authority, semantic mappings, connector capabilities, playbooks or provider state.
- Use deterministic tools for numeric calculations and validators.
- Enforce tenant and invoking-member permission scope on every call.
- Never allow the model to create SQL text that is executed directly or invoke arbitrary HTTP endpoints.
- Persist investigation trace and model/prompt/tool versions for reproducibility.
- Treat confidence as qualitative until calibrated on labelled cases; monitor false-positive and unsafe recommendation rates.

---

# 9. Playbook and execution specification

## 9.1 Playbook definition

A playbook is an immutable, versioned domain workflow definition. It declares namespace/ID, domain-pack version, trigger/case types, required evidence, preconditions, permissions, connector action capability requirements, approval policy, parameter schema, idempotency strategy, execution steps, compensation/failure path, verification conditions and test fixtures. A tenant may configure supported parameters, but the AI cannot change the active definition.

## 9.2 Initial Finance playbooks

| ID | Playbook | Preconditions and action | Approval policy | Verification |
|---|---|---|---|---|
| PB-001 | Payment-state recovery | A PayPal capture is authoritatively `COMPLETED` while the mapped merchant payment is `PENDING`; amount, currency and order relationship agree; signed event evidence, deduplication and merchant correction capability are valid. Re-deliver the internal payment event through an authorized merchant API/action. | Tenant policy MUST explicitly resolve to `REQUIRED` or `NOT_REQUIRED`. For the primary V1 demo, configure `REQUIRED` and use a separate Approver. `NOT_REQUIRED` is permitted only for a low-risk internal correction after a recorded, versioned policy decision while its exact scope is LIVE. | Re-read merchant payment and verify `COMPLETED`; persist provider and merchant evidence. No inference from HTTP success alone. |
| PB-002 | Duplicate-capture refund | Evidence proves two distinct successful captures for one intended payment/order; identify original vs duplicate; verify merchant/order mapping, currency and amount; prove no existing refund for target capture; ensure no pending dispute/hold where applicable. Same amount alone is not proof of duplicate. | Always `REQUIRED` in V1. Approver MUST be different from requester. | Perform PayPal Sandbox refund; retrieve refund/capture state and verify completed outcome. |
| PB-003 | Settlement / chargeback workflow | Demo/template placeholder only. Settlement variance and chargeback investigation may be detected, but no executable action until source rules, report accounting semantics, approval, action contract and verification conditions are fully defined and tested. | Not configured for execution. | Non-executable in V1. |

**Approval policy contract:** request creation creates an immutable REQUEST-stage `policy_decisions` row recording `approval_required`, policy version, evaluated conditions and reason. A REQUEST-stage outcome is `NEEDS_APPROVAL` when `approval_required=true`, or `ALLOW_PENDING_PRECONDITIONS` when `approval_required=false`; it is never execution authorization. When approval is required, execution is impossible until an independent approved `playbook_approvals` row exists. When approval is not required, no approval row exists and `approval_id` remains NULL through execution/grant; this is not a missing value that the caller may fill. After all required approval and action preconditions pass, a separate immutable EXECUTION-stage policy decision records `ALLOW`. A caller cannot choose the approval mode or manufacture either decision. V1 demo PB-001 is configured `REQUIRED`; PB-002 is always `REQUIRED`.

## 9.3 Request, case and execution state machines

These are separate state machines. Case status MUST NOT be overloaded to represent approval or execution status.

**Case lifecycle:**

`OPEN -> INVESTIGATING -> HUMAN_REVIEW -> ACTION_PENDING -> RESOLVED`

Permitted alternate transitions are `OPEN/INVESTIGATING/HUMAN_REVIEW -> DISMISSED`, `RESOLVED/DISMISSED -> CLOSED`, and `CLOSED -> OPEN` only through an authorized reopen operation with a reason and audit event. `ACTION_PENDING` cannot be dismissed or closed while an external side effect is in flight unless a tested cancellation/compensation path has completed. Failed or ambiguous execution returns the case to `HUMAN_REVIEW` or `OPEN`; it MUST NOT mark the case `RESOLVED`. Set `ACTION_PENDING` atomically with the request and its REQUEST-stage decision when that decision is `NEEDS_APPROVAL` or `ALLOW_PENDING_PRECONDITIONS`; otherwise the case stays in `HUMAN_REVIEW`.

**Playbook request lifecycle:**

`CREATED -> REQUEST_POLICY_EVALUATED -> NEEDS_APPROVAL -> APPROVED -> PRECONDITIONS_VALIDATED -> EXECUTION_POLICY_ALLOWED -> EXECUTION_CREATED`

For a REQUEST-stage decision with `approval_required=false`, the request may transition directly from `REQUEST_POLICY_EVALUATED` to `PRECONDITIONS_VALIDATED`. Denied, rejected, expired or cancelled requests transition to explicit terminal states (`DENIED`, `REJECTED`, `EXPIRED`, `CANCELLED`); the case returns from `ACTION_PENDING` to `HUMAN_REVIEW` when no side effect has started or after a safe failure/rejection is recorded. Both REQUEST- and EXECUTION-stage policy decisions are immutable; a changed policy requires a new decision record, and a new request where the change alters the approved action/target or material risk context.

**Execution lifecycle:**

`CREATED -> GRANT_ISSUED -> EXECUTING -> VERIFYING -> SUCCEEDED`

Alternate outcomes include `FAILED`, `RETRY_PENDING`, `OUTCOME_UNKNOWN`, `POLICY_BLOCKED` and `MANUAL_REVIEW`. `approval_id` is nullable only when the linked immutable policy decision says approval is not required. All transitions record tenant, actor/principal, timestamp, request/decision/approval references, playbook version, evidence, idempotency key and reason. No state transition bypasses preconditions, execution mode, approval policy or verification.

## 9.4 Idempotency, reliability and verification

- Every external action MUST have a tenant-scoped idempotency key unique to the logical operation.
- Persist the request, policy decision, any approval, execution row and action attempt before/around the corresponding state transition; execution state is durable before any external side effect.
- Worker MUST present a single-use POL-09 grant bound to tenant, execution, request, playbook version, `request_policy_decision_id`, `execution_policy_decision_id`, action, target and idempotency key. `execution_policy_decision_id` MUST reference an immutable EXECUTION-stage `ALLOW`; `request_policy_decision_id` is retained for provenance. `approval_id` is bound if and only if the REQUEST-stage decision requires approval.
- Grant consumption and transition to `EXECUTING` commit atomically. Reuse, expiry, target/action mismatch, policy-decision mismatch or approval mismatch fails closed.
- A repeated request with the same tenant-scoped idempotency key returns the prior request/execution result or current state; it does not repeat the side effect.
- After timeout with ambiguous outcome, re-read authoritative provider/business state before retrying. Never blindly retry a refund/action.
- PB-002 refund execution always requires explicit independent approval in V1. PB-001 follows its recorded tenant policy; the primary demo uses REQUIRED.
- Connector must expose the required action capability; authorization alone cannot invent support.
- API success is not case resolution. Re-read authoritative state and evaluate versioned verification conditions.
- Persist each action attempt, provider response/evidence reference, retry/backoff state, final state and human escalation in `execution_action_attempts` and audit.

## 9.5 Shadow mode and LIVE mode

SHADOW is the default for every tenant/domain/connector-action scope. It permits ingestion, discovery, profiling, reconciliation, case creation, investigation, recommendations, dry-run validation and audit. It prohibits every business-state-changing action across every domain. This includes internal merchant database corrections, payment-status changes, refunds, subscription mutations, inventory updates and external provider actions. A business-state-changing operation MUST NOT be re-labelled as a harmless internal correction to evade SHADOW.

Only the Tenant Owner's derived `execution_mode.manage` permission may request a transition of one exact scope to LIVE. The canonical scope key is `(tenant_id, domain_pack_id, connector_id, action_capability)`; for example, `(Tenant A, Financial Operations, internal-merchant adapter, merchant.payment_status.correct)` and `(Tenant A, Financial Operations, PayPal Sandbox adapter, refunds.create)` are different settings. An absent exact-scope row resolves to SHADOW. There is no implicit inheritance from tenant, domain, connector, sibling action, playbook or another scope; switching one action MUST NOT change any other action scope. The mode-setting API MUST require all scope-key fields and reject wildcard or ambiguous requests in V1. Every switch is audited. No release attestation is required (CHG-1.8-01). The exact-scope mode is checked at the execution stage, again when an execution grant is issued, and again by the Worker when it consumes the grant; SHADOW at any of these points means no execution. Approvals do not bypass SHADOW.

**Terminology:** SettleIQ `LIVE` means the internal policy mode permits an otherwise authorized business-state-changing action. It does not mean production credentials or a live PayPal account. The V1 demo runs SettleIQ in `LIVE` mode while the PayPal adapter remains connected to PayPal `Sandbox`. Provider environment and SettleIQ execution mode are independent settings and both must be displayed explicitly.

Second-holder confirmation for the owner switching a scoped mode to LIVE is intentionally OPEN for V1; it does not waive approval, revalidation or any other execution control.

## 9.6 Generic playbook extensibility

Platform playbook schema is domain-neutral. Domain packs register versioned templates and required connector action capabilities. Every playbook remains subject to tenant scope, least privilege, preconditions, approval policy, idempotency, durable state, independent verification and an auditable failure path.

## 9.7 V1 state-changing action scopes

Release attestation (POL-14) is retired in v1.8 (CHG-1.8-01). No developer-side issuance, signing or verification gates execution. Each state-changing capability runs under its own exact scope, and only the controls in §9.3 to §9.5 apply to it: SHADOW by default, the Tenant Owner's LIVE switch for that exact scope, the REQUEST decision, independent approval where required (POL-01), execution-stage revalidation, the single-use grant (POL-09) and post-action verification.

| Scope | Connector | Action capability | Readback dependency |
|---|---|---|---|
| Tenant A, Financial Operations | `internal-merchant` | `merchant.payment_status.correct` | `merchant.payment.read` |
| Tenant A, Financial Operations | PayPal Sandbox adapter | `refunds.create` | `refunds.read`, `captures.read` |

Each scope has its own mode and its own safety-critical tests. Switching one scope to LIVE MUST NOT make the other LIVE. Readback dependencies are read capabilities, not separate state-changing scopes. PayPal read and ingestion capabilities are validated through connector and integration tests. The SaaS reference pack's manifest, validator and fixture do not constitute an executable business action.

---

# 10. PayPal integration specification

## 10.1 Integration intent and boundaries

PayPal is the first active provider adapter in the Financial Operations pack, not the platform's default source type. Provider-specific logic remains inside the adapter. Adapter outputs preserve original evidence and normalize into the generic source-record envelope before a Finance projection is created. Browser code MUST NOT receive client secrets.

**Tenant account connection (CHG-1.7-01).** Each tenant connects its own PayPal account by supplying the client credentials of a PayPal REST app that the tenant creates and controls in its own PayPal account ("bring your own app"). SettleIQ does not operate one platform app that acts on tenants' accounts, and V1 does not use PayPal partner or multiparty onboarding (OPEN-PP-02). The V1 demonstration tenant connects a team-controlled PayPal Sandbox app the same way. The onboarding guidance MUST tell the tenant to create a dedicated app for SettleIQ with only the features the active capabilities need (transaction search and, where `refunds.create` is enabled, refunds), so the tenant can revoke or rotate it independently of its other integrations. The credentials are stored only under the tenant's namespace in the secret manager (§11.3); the source holds an opaque reference. Possession of valid credentials grants no execution authority: every state-changing PayPal action still requires the request, approval, exact-scope LIVE mode, POL-14 attestation and single-use grant chain of §9.

## 10.2 P0 capability coverage

| Capability | PayPal role | Priority |
|---|---|---|
| Historical transaction ingestion | Transaction Search and relevant reports | P0 |
| Balance reconciliation | Balance Reconciliation Report ingestion, including transaction-level fees/refunds/disputes/withdrawals visibility | P0 |
| Event monitoring | Payment/refund webhooks with signature verification and deduplication | P0 |
| Investigation | Retrieve transaction/capture/refund details | P0 |
| Resolution | Refund through authorized Sandbox API and verify resulting state | P0 controlled demo |
| Disputes API | Dispute/chargeback retrieval | P1 unless required for a tested fixture |

## 10.3 API and report usage

The implementation baseline includes:

- Transaction Search API for historical transaction retrieval and backfill.
- Balance Reconciliation Report (BRR) as a **P0 report/CSV ingestion path**, uploaded through the source file-ingestion workflow; do not describe it as an ordinary real-time REST transaction endpoint. Use a provider-generated report when available; if unavailable for the MVP fixture, use the controlled SYNTHETIC/REFERENCE fixture defined in §10.3.1. Both inputs MUST use the same parser, mapping, validation and reconciliation path. Automated SFTP/report retrieval remains P1.
- Payments API for capture/refund detail and controlled refund actions. The source baseline identifies the following operations: `GET /v2/payments/captures/{capture_id}`, `POST /v2/payments/captures/{capture_id}/refund`, and `GET /v2/payments/refunds/{refund_id}`.
- PayPal webhooks for asynchronous payment/refund lifecycle changes. The primary demo event is `PAYMENT.CAPTURE.COMPLETED`.

The v0.5 source discussion contains assumptions about Transaction Search lag, webhook retries and partner/multiparty account access. These are not treated here as independently verified service guarantees. Confirm actual current provider documentation, app approval requirements, rate limits and the team's account access before implementation. Transaction Search MUST NOT be treated as a real-time event stream: use webhooks for event-driven updates and reports/polling for reconciliation and backfill.

### 10.3.1 BRR availability and deterministic fallback policy

**P0 dependency and primary input.** The BRR remains a P0 input to the settlement fixture. The primary input is a provider-generated PayPal Balance Report CSV obtained through the merchant reporting portal or SFTP where the merchant account is entitled and the report is available. The implementation MUST NOT assume that enabling a PayPal Sandbox app automatically makes a BRR available; OPEN-PP-01 must verify actual account/report access before adapter build. A missing provider report MUST be surfaced as unavailable; it MUST NOT be silently treated as an empty report or as a successful reconciliation.

**Approved fallback for the MVP fixture.** If a provider-generated report cannot be obtained in time for deterministic P0 testing, the team MUST use a version-controlled reference BRR CSV fixture. Build its structure against the published PayPal Balance Report specification and an official PayPal sample report. Mark the fixture SYNTHETIC/REFERENCE in its filename, fixture metadata and UI, and store its version and SHA-256 checksum. It MUST NOT contain real tenant credentials or personal/payment data and MUST NOT be represented as a report generated by the tenant's PayPal account, as provider evidence or as production source evidence.

**Fixture content and parser requirements.** The fixture MUST include deterministic records that exercise sales/captures, fees, refunds and adjustments. The fixture manifest MUST state the expected opening/closing balance and the accounting treatment of each record, including whether fees/refunds are already included in a reported net amount. Parser logic MUST use column/record names rather than positional assumptions, tolerate additional unknown columns without dropping original evidence, preserve UTF-8 and the original file hash, and reject malformed required fields or inconsistent totals. The fixture MUST be deterministic and stable across test runs.

**Common ingestion and evidence boundary.** Provider-generated and SYNTHETIC/REFERENCE BRR files MUST enter through the same `source.file.upload`, raw-evidence preservation, parser, mapping, validation and reconciliation pipeline. Evidence provenance MUST distinguish `provider_report` from `synthetic_reference` at every stage. Synthetic reference data may satisfy deterministic parser, mapping and settlement-fixture acceptance tests only; it MUST NOT satisfy any acceptance criterion claiming actual provider report retrieval or actual tenant reconciliation. The UI and demo MUST visibly disclose which mode is in use.

**Acceptance evidence.** AC-REC-07 and AC-PP-11 MUST exercise the common pipeline with the provider report when available and with the approved reference fixture. Tests MUST verify included-in-net semantics and prove that fees/refunds already represented in net amounts are not subtracted a second time. The test report records fixture version, checksum, provenance mode and expected reconciliation result.

## 10.4 Webhook ingestion requirements

- Resolve `source_id` through a narrowly privileged server-side source-registration lookup that is not exposed as a general tenant-data query. This lookup returns the trusted tenant ID, connector instance and registered PayPal webhook ID.
- Verify the PayPal signature against the registered webhook ID and provider transmission metadata before trusting event content. Reject invalid signatures before tenant derivation; they MUST NOT create tenant `raw_evidence`/`webhook_inbox` rows or alter canonical state. Forensics, if required, use a separate platform security ingress log with bounded untrusted metadata only.
- Only after successful signature verification, derive tenant context from the trusted source registration. Begin a database transaction and set tenant context transaction-locally (`SET LOCAL` or equivalent); all subsequent reads/writes use ordinary RLS-protected repositories.
- Never trust `tenant_id` in the webhook payload, URL query, browser header or caller-supplied metadata. `source_id` is a lookup key, not proof of tenant authorization.
- Persist the original body, event ID/type, source, trusted tenant, successful signature result, receipt/provider timestamps and payload hash in `raw_evidence`/`webhook_inbox` only after signature verification and trusted tenant derivation, and before acknowledging receipt.
- Deduplicate repeated delivery by `(tenant_id, source_id, provider_event_id)`; process asynchronously and idempotently. Separate durable receipt from downstream domain processing.
- Tolerate delayed/out-of-order events. Do not assume delivery order is business order.
- Use a trusted provider read for critical current state before dependent actions.
- Do not assume webhook delivery means merchant-side processing completed.
- Expose failed processing, replay/backfill and dead-letter/human review state. A retry MUST reuse the original event identity and not duplicate the downstream business action.

## 10.5 End-to-end Sandbox scenario A: payment-state recovery

1. A customer completes a $100 PayPal Sandbox payment.
2. SettleIQ receives and validates `PAYMENT.CAPTURE.COMPLETED`, stores the original event and schedules processing.
3. Merchant PostgreSQL intentionally remains `PENDING` (for the controlled test fixture).
4. Active ontology says PayPal is authoritative for capture completion, while the merchant database is authoritative for internal order/payment state.
5. Finance engine creates `STATUS_MISMATCH` with observation/evidence references.
6. Agent investigates the event, merchant state and mapping; it recommends PB-001 with evidence and approval requirements.
7. Operator with `playbook.request` creates the request. Policy validates amount/currency/order mapping, action capability and preconditions.
8. If policy requires approval, an independent Approver decides it. The agent does not create or approve the request.
9. Worker receives the single-use execution grant and replays the internal merchant event.
10. SettleIQ re-reads the merchant record and resolves the case only after it verifies `COMPLETED`.

## 10.6 End-to-end Sandbox scenario B: duplicate capture refund

1. Create a test payment with two distinct successful capture IDs for one intended order/payment.
2. Prove duplicate status using IDs, order linkage and event/provider details; equal amounts alone are insufficient.
3. Verify the chosen duplicate capture has no existing refund and that no domain/policy hold prevents the action.
4. Agent recommends PB-002. Operator requests it. Authorized Approver reviews evidence and approves or rejects.
5. Before execution, the Tenant Owner switches only `(Tenant A, Financial Operations, PayPal Sandbox adapter, refunds.create)` from SHADOW to SettleIQ LIVE and record the transition. This MUST NOT change the PB-001 internal merchant-correction scope or any other action scope. The provider environment remains PayPal Sandbox.
6. Worker issues the Sandbox refund using the exact capture and idempotency key only after rechecking that the exact `refunds.create` scope is LIVE, approval is valid and preconditions still hold.
7. Worker retrieves resulting refund/capture state and verifies completion before marking the case resolved.

---

# 11. Multi-tenant security and data protection

## 11.1 Tenant isolation

- Tenant context is derived server-side from the authenticated principal and selected tenant membership; a browser-supplied tenant ID is a selector, never proof of access.
- Every tenant-owned row carries `tenant_id`; PostgreSQL RLS is mandatory defense-in-depth. Service-layer authorization and RLS must both enforce isolation.
- All queries, background jobs, retrieval, caches, object-storage paths, webhook processing and evidence references are tenant-scoped. Never rely on frontend filtering.
- A resource belonging to another tenant returns 404 rather than 403 to avoid confirming its existence. A nonexistent tenant and a tenant for which the caller has no ACTIVE membership MUST produce the same externally observable 404 response/body shape and materially equivalent response behavior; do not return `NOT_MEMBER` to an untrusted client.
- Cross-tenant grants, cache keys, agent context and idempotency keys must not collide or be reused.
- Platform support has no default tenant business-data access; break-glass requires POL-10 and an audit trail.

## 11.2 Authentication and session controls

Use a maintained authentication implementation. Registration and verification endpoints are rate-limited. Verification and invitation tokens are random, high entropy, hashed at rest, single-use and expire. Password/session policy, MFA requirements and exact JWT/session transport are OPEN implementation choices; regardless of transport, permissions are resolved from current membership/grants on each request or by a bounded, revocable cache with explicit invalidation. Stale token claims must not retain access after membership or grant revocation.

## 11.3 Secrets and external integrations

Credentials are represented in application/database configuration by opaque secret references. Secret values live only in a secret manager or approved deployment secret store. They must not appear in logs, browser payloads, model prompts, raw debug output or audit events. Credential creation/rotation is permission-gated and audited. The connector manager exposes only the minimum action capability and secret handle required by its adapter.

**Tenant-namespaced secret references (CHG-1.7-02).** A secret reference names a secret within its tenant's namespace only. The server resolves it under the tenant ID taken from the trusted context of the source that holds it, never from the reference text, so the same reference names a different secret in every tenant and no reference can be written to reach another tenant's secret or a platform secret. The deployment secret store keeps each tenant's source secrets under a per-tenant path. A deployment-wide secret shared by all tenants, such as a process environment variable, MUST NOT be resolvable as a tenant source secret in production; it is permitted for local development and CI only. A reference that does not resolve within the tenant fails closed, and neither the reference nor the value appears in the error.

**Write-only credential intake (CHG-1.7-01).** Before a tenant other than the SYNTHETIC demonstration tenant connects a provider account, an Integration Admin MUST be able to submit the provider credentials through a write-only intake: the value is written directly to the secret manager under the tenant's namespace and the source stores the resulting reference. The value is never returned, echoed, logged, audited, stored in an ordinary database field or supplied to a model; rotation replaces it and stops the old value being used, and deletion revokes the reference. Until the intake exists, the platform operator places credentials in the deployment secret store under the tenant's namespace, and only the demonstration tenant is connected.

## 11.4 Evidence, privacy, anti-enumeration and audit

Preserve original evidence immutably with hashes, retrieval context, source timestamps and access controls. Sensitive evidence reads and critical decisions are audited. Logs are structured and redact credentials, personal data not required for operation and sensitive payload fields. Define retention, deletion/legal hold and backup policies before production; the MVP must at least retain the audit/evidence needed to prove the tested workflow.

**Tenant anti-enumeration:** API responses MUST NOT disclose whether an inaccessible tenant or resource exists. Requests by a non-member and requests for a nonexistent tenant use the same external 404 response code and response schema. The internal authorization/audit record MAY retain `NOT_MEMBER`, `TENANT_NOT_FOUND` or other specific reason codes, but these details MUST be restricted to trusted logs and must not appear in client-visible errors, timing headers or response metadata.

## 11.5 Authorization decision sequence

1. Authenticate principal and require verified identity for tenant operations.
2. Resolve tenant using server-side identity/membership or a trusted source-registration path for signed provider webhooks.
3. For tenant APIs, make nonexistent and non-member tenant outcomes indistinguishable externally; record precise internal reason only in protected audit.
4. Establish server-derived tenant context for the transaction and apply RLS before accessing tenant-owned rows.
5. Resolve current effective permissions and matching semantically valid scope; apply DENY precedence.
6. Check the route/tool's one required permission (or explicit PUBLIC/IDENTITY designation).
7. Evaluate applicable policy rules, case/request state, deterministic preconditions, approval requirement, SHADOW/LIVE mode, connector capability and idempotency independently.
8. Issue a one-use grant only after every gate passes. Execute only under the Worker principal and exact grant.
9. Persist authorization decision, state transition, evidence and outcome to tenant audit.

Client-visible authorization failures use only non-enumerating responses: `404 NOT_FOUND` for unknown or inaccessible tenant/resource, `403 MISSING_PERMISSION` with a permission key only after tenant membership and resource ownership have been established, and `403 POLICY_DENIED` with a policy code only where disclosure cannot reveal another tenant's existence. Detailed reason codes such as `NOT_MEMBER`, exact tenant lookup outcome and internal policy traces are protected audit data, not external response fields. Unknown routes/tools/permission keys fail closed.


---

# 12. API surface and contracts

## 12.1 API baseline

Use a versioned REST API. Each route declares exactly one registered permission, or is explicitly marked PUBLIC or IDENTITY. The route-to-permission map is exported and table-tested in CI. Every tenant-owned handler establishes server-derived tenant context before repository access. The API uses one canonical error envelope and does not disclose inaccessible tenant/resource existence. Request bodies MUST NOT accept authoritative `tenant_id`, `requested_by`, `approver_id`, `approval_required`, `request_policy_decision_id`, `execution_policy_decision_id`, `execution_id` or execution-grant values from an untrusted client; all are derived by server-side identity, policy and workflow services.

Before frontend/backend integration, freeze the concrete OpenAPI schemas, pagination, authentication transport, idempotency header format, rate limits and backward-compatibility policy. These implementation details remain OPEN; they do not relax the normative behavior below.

## 12.2 V1 endpoint catalogue

| Method / endpoint | Required permission / identity | Purpose / contract |
|---|---|---|
| `POST /api/auth/register`, `POST /api/auth/verify-email` | PUBLIC | Register and verify global identity; rate-limited. |
| `POST /api/tenants` | IDENTITY (verified) | Create tenant, initial ACTIVE membership and owner pointer atomically. |
| `POST /api/invitations/{token}/accept` | IDENTITY (verified matching email) | Atomically consume valid invite and install allowed grants. |
| `GET /api/me/permissions?tenant_id=` | IDENTITY | Return effective permissions/scopes for selected tenant; no trust in caller-supplied claims. |
| `GET /api/permissions`, `GET /api/permission-sets` | `role.read` | Read registered catalogue and system-managed sets. |
| `POST /api/tenants/{id}/invitations` | `member.invite` | Create pending invitation with grantable permission sets/scopes. |
| Invitation list/resend/revoke routes | `member.invitation.manage` | Manage pending invitation lifecycle; resend rotates token. |
| `GET /api/tenants/{id}/members` | `member.read` | List tenant members/effective grants. |
| `PATCH /api/tenants/{id}/members/{user}/grants` | `member.role.assign` | Grant/revoke only tenant-grantable sets and meaningful scopes. |
| `DELETE /api/tenants/{id}/members/{user}` | `member.remove` | Remove membership subject to owner/last-holder constraints. |
| `POST /api/tenants/{id}/ownership-transfer` | `tenant.ownership.transfer` | Atomic owner pointer transfer; only route that changes owner. |
| `PUT /api/tenants/{id}/execution-mode` | `execution_mode.manage` | Request a mode change for exactly one `(domain_pack_id, connector_id, action_capability)` scope. All three fields are required; wildcard/omitted scope fields are rejected in V1. Missing exact-scope rows resolve to SHADOW. Changing one scope cannot mutate another. |
| `GET /api/tenants/{id}/execution-readiness` | `execution.read` | Read-only list of the exact scopes the tenant's active packs declare, with each scope's current mode, for UI display. |
| `GET /api/connectors/capabilities` | `connector.read` | Expose canonical registered connector capability keys. |
| `GET/POST /api/sources` | `source.read` / `source.create` | Read/create source registry entries. |
| `POST /api/sources/{id}/discover`, `/sync` | `source.discover` / `source.sync` | Discover/profile or sync/backfill. |
| `POST /api/sources/{id}/files` | `source.file.upload` | Upload bank CSV or P0 Balance Reconciliation Report CSV. |
| `PUT /api/sources/{id}/credentials` | `source.credential.manage` | Store/update opaque secret reference only; the reference must resolve within the source's tenant (§11.3). |
| `PUT /api/sources/{id}/credential-value` | `source.credential.manage` | Write-only credential intake (§11.3): writes the submitted value to the secret manager under the tenant's namespace and stores the resulting reference; responds with credential status only. Required before a tenant other than the SYNTHETIC demonstration tenant connects a provider account. |
| `PUT /api/sources/{id}/webhooks` | `source.webhook.manage` | Register webhook metadata/ID; never store unverified arbitrary tenant context. |
| `POST /api/webhooks/paypal/{source_id}` | PUBLIC + valid PayPal signature | Resolve trusted source registration, verify signature, derive tenant context, then persist through RLS. |
| `GET/POST /api/domain-packs` | `domain_pack.read` / `domain_pack.manage` | List/activate/manage compatible packs. |
| `POST /api/ontology/generate`, `/validate`, `/publish` | `ontology.generate`, `ontology.validate`, `ontology.publish` | Draft, hash-bound validation, explicit `confirm_publish=true` publication under POL-12. |
| `GET /api/ontology` | `ontology.read` | Read active immutable composition/version. |
| `POST /api/domain-runs` | `domain_run.create` | Start version-pinned domain run. |
| `GET /api/domain-cases` | `case.read` | List cases within tenant. |
| `PATCH /api/domain-cases/{id}` | `case.triage` | Assign/comment/escalate/dismiss with reason and valid case-state transition. |
| `POST /api/domain-cases/{id}/reopen` | `case.triage` | Reopen a CLOSED case with reason and audit. |
| `POST /api/entity-resolution/{id}/decision` | `entity_resolution.review` | Accept/reject ambiguous identity link. |
| `POST /api/agent/investigate/{id}` | `case.investigate` | Start delegated investigation; Agent may only persist bounded investigation metadata. |
| `GET /api/investigations/{id}` | `investigation.read` | Read structured result and authorized tool trace. |
| `POST /api/playbooks/{id}/requests` | `playbook.request` | Human Operator creates a durable request; server creates immutable policy decision. |
| `GET /api/playbook-requests/{id}` | `playbook.read` | Read request status and policy decision. |
| `POST /api/playbook-requests/{id}/approve` | `playbook.approve` | Independent human Approver decision on request/policy decision (POL-01). |
| `POST /api/playbook-requests/{id}/reject` | `playbook.reject` | Human rejection with reason. |
| `POST /internal/executions/{id}/run` | Worker principal only; POL-09 `playbook.execute` | Private service-to-service route; requires exact unconsumed grant. No browser/UI exposure. |
| `GET /api/executions/{id}` | `execution.read` | Read attempts, execution state and verification. |
| `GET /api/audit/{id}` | `audit.read` | Read permitted audit trail. |
| `POST /api/platform/tenants/{id}/support-requests` | `platform.support.request` | Platform Operator requests bounded read-only break-glass access with reason/ticket/expiry. |
| `POST /api/platform/support-requests/{id}/approve` | `platform.support.approve` | Independent platform security approver decision; requester cannot approve own request. |

Human users MUST NOT call an execution endpoint. The Operator requests a playbook; the policy engine evaluates it; a human Approver decides only when required; a Worker starts an already-created execution only after a single-use POL-09 grant is issued. The internal Worker route MUST be network/service-principal isolated and MUST reject human principals before any action is attempted. Demonstrations of denial use a service/integration test harness, not a human-facing execute route.

## 12.3 Frontend permission contract

`GET /api/me/permissions` is the source of truth for the frontend's action visibility and availability. UI may hide controls when a permission is absent, but hiding is not authorization. The API rechecks permission, scope, principal type and policy on every operation. Role/set names MUST NOT drive frontend authorization logic.

---

# 13. Frontend and Business Semantic Studio

## 13.1 Core workspaces

- **Tenant workspace and Team Management:** current tenant, active membership, member grants, pending invitations, resend/revoke, grant changes and owner-transfer workflow (owner-only).
- **Source Management:** registered connectors, declared capabilities, credentials status (never secret value), discovery/profile, sync state, webhook status and file uploads.
- **Ontology Studio:** platform core + selected packs + tenant extensions; class/property/relationship editor; schema-to-semantic bindings; authority; validation and exact-candidate publication confirmation.
- **Domain Operations:** run history, canonical entities, property observations, resolved views, relationship instances, reconciliation cases and scope-aware discrepancy views, including explicit case lifecycle states and reasoned dismiss/close/reopen actions.
- **Investigation:** evidence-grounded agent summary, observed/inferred distinction, tool trace, recommendation, missing evidence and playbook validation output.
- **Playbook Requests:** Operator request, Approver decision, policy outcome, Worker execution, verification and audit history.

## 13.2 UX requirements

Show source provenance and timestamps alongside values. Show all mapped observations and the selected resolved value; do not hide conflicts behind one displayed number. Explicitly label authority state, stale values and human-review requirement. Distinguish `INTRA_ENTITY` from `INTER_ENTITY` cases. Distinguish agent recommendation from a human request and approval. Publication UI must show the candidate version/hash and validation run, require an explicit confirmation step and reject any stale candidate. All UI action availability is based on effective permissions returned by the backend.

---

# 14. End-to-end operating workflows

## 14.1 New tenant and team onboarding

1. A real person registers with an email, verifies it and creates a tenant. The owner pointer and initial ACTIVE membership commit atomically.
2. The Tenant Owner invites three distinct real email identities for the V1 demo: `integration_admin`, `domain_operator` and `approver`. No fixed addresses, seeded accounts or email-to-role mappings are permitted.
3. Each invitee accepts through their own verified mailbox; show active membership and effective permission sets in Team Management.
4. The Integration Admin is responsible for PayPal credential references and webhook registration. The Business Admin/Owner may create the source registry record and review configuration metadata, but does not receive secret-management or webhook-management rights by inheritance.
5. Discovery, synchronization and domain runs do not occur during identity onboarding. They follow source setup and ontology publication under §14.2 and §14.3.
6. The Approver reviews playbook requests and does not approve any request they personally created.
7. All invitations, grants, configuration changes and role/membership lifecycle events are audited. No pre-seeded identities are used.

## 14.2 Source and semantic onboarding

1. Business Admin/Owner creates the source registry entry with connector-neutral source metadata and declared capability set.
2. The tenant creates a dedicated PayPal REST app in its own PayPal account (§10.1). Integration Admin stores its credentials in the secret manager under the tenant's namespace (§11.3), validates authentication and registers the PayPal webhook using `source.credential.manage` and `source.webhook.manage`.
3. Integration Admin or authorized Operator uploads the bank CSV and the P0 Balance Reconciliation Report CSV through `source.file.upload`; automated SFTP/report retrieval remains P1.
4. As Integration Admin, discover schema/metadata and profile representative records.
5. Generate a draft platform/domain/tenant ontology composition and mapping/authority proposals.
6. Human reviews semantics and bindings. Create separate mapping entries for multiple sources targeting one comparable property.
7. Run platform SHACL and active pack validators against exact candidate content.
8. Authorized Business Admin/Owner explicitly confirms and publishes exact validated hash; immutable snapshot and audit event are written.

## 14.3 Reconciliation and investigation

1. After §14.2 publication, the Integration Admin or Domain Operator (`source.sync`) starts ingestion; raw evidence and canonical records retain provenance.
2. Project eligible source records into active domain pack(s).
3. Resolve record-to-entity candidates and persist accepted/ambiguous decisions.
4. Create property observations and derive property views using fact-specific authority.
5. After ingestion, projection, entity resolution and property views, the Domain Operator (`domain_run.create`) starts the deterministic engine, which creates scope-tagged cases.
6. Agent retrieves authorized evidence, classifies likely cause and recommends an eligible playbook.
7. Operator reviews and creates a request; Approver decides where policy requires it.
8. Worker validates execution grant, executes idempotently and verifies current authoritative state.
9. Audit records full lineage; case resolves only when verification conditions pass.

## 14.4 Second-domain extensibility proof

A tenant activates the SaaS Subscription Lifecycle reference pack, either alone or beside Financial Operations (AC-OPEN-03). It ingests subscription, renewal, invoice and entitlement records through the same generic source-record envelope, detects a renewal/invoice state inconsistency using pack-specific semantics, and uses the shared generic case/evidence/approval/workflow/audit infrastructure. Each pack's domain run projects only the classes its own identity policy declares. The pack declares no connector action, so it has no execution-mode scope. That the pack loads, validates, activates and runs without Financial Operations is proven by automated tests (AC-OPEN-07); the demonstration (§18) runs both packs in one tenant. This is a reference-pack proof of platform composition, not a claim of production SaaS support.

---

# 15. Non-functional requirements

| ID | Requirement |
|---|---|
| NFR-001 | Tenant isolation is enforced by backend authorization and database RLS, including background processing and retrieval. |
| NFR-002 | Financial arithmetic is decimal-safe, currency-explicit and deterministic. |
| NFR-003 | Runs and executions are reproducible from pinned ontology, pack, mapping, source, rule and playbook versions. |
| NFR-004 | Every normalized value and finding is traceable to source and raw evidence. |
| NFR-005 | Webhook and external action processing is idempotent and duplicate-safe. |
| NFR-006 | Published ontology/domain/playbook versions are immutable. |
| NFR-007 | Approval, policy checks, execution mode and verification are enforced server-side. |
| NFR-008 | LLM outputs are evidence-grounded and cannot directly mutate business state. |
| NFR-009 | Provider-specific logic is isolated in connector adapters. |
| NFR-010 | Concurrent publication, ownership transfer and state transitions fail closed with transactional consistency. |
| NFR-011 | Background work is durable and supports retry/replay with duplicate protection. |
| NFR-012 | Logs/metrics expose ingestion, queue, domain-run, agent, policy, action and verification failures (production depth P1). |
| NFR-013 | Secrets are stored outside source code and ordinary database fields; logs/prompts redact secret values. |
| NFR-014 | Backups, recovery objectives, data retention, deletion and security incident handling are defined before production. |
| NFR-015 | Quantitative latency, throughput and availability targets are based on measured workload and agreed before production; not fabricated in this MVP baseline. |
| NFR-016 | Generic source records do not require finance fields or closed finance enums. |
| NFR-017 | Connector capability declarations are explicit and validated at runtime. |
| NFR-018 | Domain pack registration/composition is versioned and namespace-safe. |
| NFR-019 | Adapter and domain-pack interfaces remain stable and documented as the platform expands. |
| NFR-020 | Multiple source bindings and provenance-bearing observations remain separately queryable. |
| NFR-021 | Entity resolution decisions and reconciliation findings are reproducible and reviewable. |
| NFR-022 | Every finding declares reconciliation scope and affected semantic references. |
| NFR-023 | Request, policy-decision, approval, execution, grant, action-attempt, investigation and tool-trace data is durable and tenant-scoped; no correctness-critical transition depends on process memory. |
| NFR-024 | Retired in v1.8 (CHG-1.8-01). Execution readiness is the exact-scope mode alone (§9.5). |
| NFR-025 | Externally visible tenant authorization errors do not reveal tenant/resource existence; detailed reasons are retained only in protected audit. |

---

# 16. Acceptance criteria and verification plan

Acceptance criteria are outcome-based. The IDs below retain traceability to the source requirements while placing them in one test contract. Unless test evidence is attached, each is **NOT VERIFIED**; the document itself does not assert implementation completion.

## 16.1 Tenant, IAM and security criteria

| ID | Acceptance criterion |
|---|---|
| AC-TEN-01 | Creating two businesses produces separate tenants, memberships, ontology versions, source configurations, records, playbooks and audit histories. |
| AC-TEN-02 | Tenant A cannot read or mutate Tenant B records through IDs, query parameters, filters or agent tool arguments. |
| AC-TEN-03 | Tenant context is derived from the authenticated principal and applied inside each database transaction. |
| AC-TEN-04 | Connector secrets are never returned to the browser or supplied to the LLM. |
| AC-DB-01 | Every tenant-owned request, policy decision, approval, execution, grant, action attempt, investigation, tool trace and webhook inbox record has non-null `tenant_id`, enforced foreign keys and RLS. |
| AC-INV-01 | Investigation result and each tool trace are durably stored with tenant, case, invoker, model/prompt/tool versions, evidence references, result and outcome; writes remain append-only and bounded. |
| AC-IAM-01 | A verified person can register/create a tenant and atomically become its initial owner; no seeded account required. |
| AC-IAM-02 | Business Admin can invite a real email and assign permitted permission set(s)/scope. |
| AC-IAM-03 | Acceptance requires a valid, unexpired, unused invitation and verified email matching the invited address. |
| AC-IAM-04 | Existing SettleIQ user joins another tenant without duplicate global account and has tenant-independent grants. |
| AC-IAM-05 | Revoked, expired, used or superseded invitation tokens are rejected. |
| AC-IAM-06 | Unauthorized user cannot invite, assign grants, list members or mutate another tenant membership. |
| AC-IAM-07 | Grant changes and membership revocation/suspension affect authorization on the next request; stale token claims do not preserve access. |
| AC-IAM-08 | Same user can have different permission sets in different tenants; no cross-tenant grant leakage. |
| AC-IAM-09 | Delivery failure is visible/retryable and does not create active membership. |
| AC-IAM-10 | Invite, accept, resend, revoke, grant change and member removal are audited. |
| AC-IAM-11 | Demo does not use pre-seeded accounts, hard-coded emails or fixed email-to-set mappings. |
| AC-OWNER-01 | Every tenant has exactly one `owner_user_id` referencing an ACTIVE member. |
| AC-OWNER-02 | Invitation and generic grant APIs reject owner set and owner-reserved permissions. |
| AC-OWNER-03 | Only current owner can transfer; owner pointer and effective permissions change atomically. |
| AC-OWNER-04 | Former owner immediately loses derived owner permissions; Business Admin access remains only if separately granted. |
| AC-OWNER-05 | Current owner cannot be removed/suspended before transfer; concurrent transfers cannot create zero or multiple owners. |

## 16.2 Permission authorization criteria

| ID | Acceptance criterion |
|---|---|
| AC-AUTHZ-01 | CI fails if authorization code references permission-set names or role-based guards. |
| AC-AUTHZ-02 | Adding a system-managed permission set as seed data changes access on the next request without code change. |
| AC-AUTHZ-03 | Table-driven tests generated from route-to-permission map verify missing permission yields `MISSING_PERMISSION` and held permission passes the permission gate. |
| AC-AUTHZ-04 | A principal with both request and approve permissions cannot approve/reject their own request (`POLICY_DENIED`, POL-01). |
| AC-AUTHZ-05 | Business Admin cannot assign owner set/reserved permissions; tenant APIs cannot assign platform permissions. |
| AC-AUTHZ-06 | Worker-only permissions cannot be granted to humans. Agent may append investigation results/tool traces and proposed classification metadata only; it cannot mutate business state, case lifecycle, requests, approvals, execution, source records, ontology or policy. |
| AC-AUTHZ-07 | Agent returns no raw evidence when invoker lacks `evidence.raw.read`. |
| AC-AUTHZ-08 | Worker requires a single-use, expiring, scope-bound grant. Both `request_policy_decision_id` and `execution_policy_decision_id` are mandatory; `execution_policy_decision_id` MUST reference an immutable EXECUTION-stage `ALLOW`. `approval_id` is mandatory iff the REQUEST-stage decision requires approval. No grant is issued in SHADOW or without POL-14 readiness. |
| AC-AUTHZ-09 | Removing/suspending the last active holder of `tenant.ownership.transfer` or `member.role.assign` is rejected, while owner-pointer invariant is preserved. |
| AC-AUTHZ-10 | Grant scoped to one domain pack does not authorize another; max-amount condition rejects over-limit approval. |
| AC-AUTHZ-11 | Break-glass requires reason/ticket/expiry and an independent `platform.support.approve` decision; requester cannot approve own request; access is scoped/read-only by default, audited and stops working at expiry. |
| AC-AUTHZ-12 | Disabling a domain pack makes its namespaced permissions ineffective for that tenant. |
| AC-AUTHZ-13 | `GET /api/me/permissions` permissions and scopes match backend allow/deny for every protected route in the permission map; CI verifies UI action visibility, including scope conditions. The endpoint returns effective permissions only for ACTIVE tenant members; non-member and nonexistent tenant IDs return indistinguishable 404 responses. |
| AC-AUTHZ-14 | Allow/deny decisions for sensitive/critical permissions appear in audit with permission and policy IDs. |
| AC-AUTHZ-15 | SHADOW blocks all business-state-changing actions in every domain, including internal merchant correction; no approval bypasses this. |
| AC-AUTHZ-16 | Retired in v1.8 (CHG-1.8-01); POL-14 was removed. The mode checks it required continue under AC-AUTHZ-15 and AC-EXEC-MODE-04. |
| AC-AUTHZ-17 | Human principals are rejected by the Worker service before any side effect; the internal Worker route is inaccessible from browser/client networks. |
| AC-AUTHZ-18 | Scope validation permits only scope types declared for a permission; tenant-level permissions reject domain/source/playbook scopes, and empty `narrowable_scopes` is supported. |
| AC-AUTHZ-19 | Externally, nonexistent tenant and non-member tenant requests return identical 404 response code/schema; detailed reasons remain protected audit data. |
| AC-REL-01 | Retired in v1.8 (CHG-1.8-01); POL-14 release attestation was removed. |
| AC-REL-02 | Retired in v1.8 (CHG-1.8-01); POL-14 release attestation was removed. |
| AC-REL-03 | Retired in v1.8 (CHG-1.8-01); POL-14 release attestation was removed. |
| AC-REL-04 | Retired in v1.8 (CHG-1.8-01); POL-14 release attestation was removed. |
| AC-REL-05 | Retired in v1.8 (CHG-1.8-01); POL-14 release attestation was removed. |
| AC-REL-06 | Retired in v1.8 (CHG-1.8-01); POL-14 release attestation was removed. |
| AC-REL-07 | Retired in v1.8 (CHG-1.8-01); POL-14 release attestation was removed. |
| AC-REL-08 | Retired in v1.8 (CHG-1.8-01); POL-14 release attestation was removed. |
| AC-REL-09 | Retired in v1.8 (CHG-1.8-01); POL-14 release attestation was removed. |
| AC-REL-10 | Retired in v1.8 (CHG-1.8-01); POL-14 release attestation was removed. |
| AC-REL-11 | Retired in v1.8 (CHG-1.8-01); its SHADOW, approval, intent and precondition checks continue under AC-EXEC-MODE-04. |
| AC-REL-12 | Retired in v1.8 (CHG-1.8-01); POL-14 release attestation was removed. |

## 16.3 Ontology and semantic criteria

| ID | Acceptance criterion |
|---|---|
| AC-ONT-01 | PostgreSQL discovery exposes tables, columns, types, keys, foreign keys, uniqueness, nullability and indexes; generic discovery can represent equivalent metadata from other connector types. |
| AC-ONT-02 | Profiling exposes null distribution, cardinality, duplicate identifiers, numeric ranges, currency, temporal and status distributions. |
| AC-ONT-03 | Ontology generation creates a draft and never auto-publishes. |
| AC-ONT-04 | Foreign key is represented as structural evidence, not silently promoted into an unreviewed business rule. |
| AC-ONT-05 | Publication is blocked if SHACL OR any active domain-pack validator fails for the exact candidate content hash. Per-validator results are reported separately; Finance rules run only when Finance pack is active. |
| AC-ONT-06 | Publication creates an immutable version only after authorized human approval/confirmation. |
| AC-ONT-07 | Run continues with its pinned ontology/pack/mapping/rule snapshots after a newer version is published. |
| AC-ONTO-PUB-01 | Publishing without `confirm_publish=true` is rejected even if caller has `ontology.publish`. |
| AC-ONTO-PUB-02 | Publish rejects failed validation or content-hash mismatch between validation and candidate. |
| AC-ONTO-PUB-03 | Any edit after validation invalidates that validation for publication. |
| AC-ONTO-PUB-04 | Successful publish creates immutable version/snapshot and audits publisher, tenant, version, hash, validation run, timestamp and outcome. |
| AC-ONTO-PUB-05 | Concurrent/stale publication fails closed and never overwrites a published version. |

## 16.4 Canonical data, identity and reconciliation criteria

| ID | Acceptance criterion |
|---|---|
| AC-REC-01 | PayPal Sandbox, merchant PostgreSQL and bank CSV normalize through generic source envelope and project into Finance domain. |
| AC-REC-02 | Raw payload remains retrievable and is not overwritten by normalized values. |
| AC-REC-03 | Exact matching resolves records when valid shared identifiers exist. |
| AC-REC-04 | Relationship-based matching traverses approved ontology relationships. |
| AC-REC-05 | Probabilistic matching cannot bypass currency, status or order-relationship hard constraints. |
| AC-REC-06 | Ambiguous identity matches remain reviewable/unmatched; never force-resolved. |
| AC-REC-07 | P0 Balance Reconciliation Report CSV is uploaded and mapped for the settlement fixture; included-in-net flags/accounting semantics are tested and fees/refunds already included in net are never double-subtracted. Provider BRR and the SYNTHETIC/REFERENCE fixture MUST pass identical parser/mapping/reconciliation tests; the reference fixture is labelled synthetic (§10.3.1). |
| AC-REC-08 | Every finding includes affected records, evidence, expected/observed values, method and resolution state. |
| AC-REC-09 | Deterministic fixtures produce every initial Finance discrepancy type. |
| AC-REC-10 | Multiple independently versioned source bindings may target one semantic property; each record creates a distinct provenance-bearing observation. |
| AC-REC-11 | Source records link to canonical entities only through recorded identity-resolution decisions. |
| AC-REC-12 | Resolved property view applies fact authority while preserving non-authoritative observations and never overwrites raw evidence. |
| AC-REC-13 | Conflicting comparable observations for one entity/property create `INTRA_ENTITY` finding with observations, provenance, time, authority and values. |
| AC-REC-14 | Invariant across distinct entities creates `INTER_ENTITY` finding with entities, relationship instances, evidence and rule version. |
| AC-REC-15 | Different semantics, units/currencies or valid-time windows are not compared as equivalent facts. |
| AC-REC-16 | Missing/overlapping/disputed authority creates explicit unresolved/review state; ingest order/model score cannot silently choose a value. |
| AC-WEBHOOK-01 | PayPal webhook tenant context is derived from trusted `source_id` registration only after signature verification; transaction-local tenant context is then established before RLS-protected persistence. Payload tenant IDs are ignored. |
| AC-CASE-01 | Case lifecycle transitions conform to §9.3; dismiss, close and reopen require permission, valid source state, reason and audit. |
| AC-CASE-02 | Case cannot be dismissed/closed during an in-flight side effect; failed/ambiguous execution does not mark the case RESOLVED. |

## 16.5 AI and playbook criteria

| ID | Acceptance criterion |
|---|---|
| AC-AI-01 | Agent tools return only tenant-authorized records/ontology context. |
| AC-AI-02 | Agent response includes case ID, likely cause, evidence IDs, recommended playbook, qualitative confidence and approval requirement. |
| AC-AI-03 | Agent cannot execute arbitrary SQL or direct provider calls outside controlled tools. |
| AC-AI-04 | Evidence is independently retrievable without relying on model explanation. |
| AC-AI-05 | Agent retrieves only the subgraph/evidence needed for current case. |
| AC-AGENT-01 | Agent registry contains no request, approval, rejection or execution tool. |
| AC-AGENT-02 | Agent output cannot create request, approval or execution IDs and conforms to response schema. |
| AC-AGENT-03 | Only human with `playbook.request` creates request; authorized human decides; Worker executes only under POL-09. |
| AC-PLY-01 | Ineligible playbook is rejected if trigger, evidence, precondition or permission check fails. |
| AC-PLY-02 | PB-002 is not executable merely because two captures have the same amount. |
| AC-PLY-03 | Refund execution requires approval. |
| AC-PLY-04 | Duplicate request with same tenant-scoped idempotency key does not repeat side effect. |
| AC-PLY-05 | Every transition and action attempt is durably persisted. |
| AC-PLY-06 | Case cannot resolve until post-action verification passes. |
| AC-PLY-07 | SHADOW supports discovery, ingestion, reconciliation, investigation and recommendations but executes no business-state changes in any domain, including merchant database correction. |
| AC-PLY-08 | PB-001 approval is determined by immutable tenant policy, not request input (primary demo REQUIRED). Both `request_policy_decision_id` and `execution_policy_decision_id` are mandatory; the latter MUST be an EXECUTION-stage `ALLOW`. `approval_id` is NULL only when the REQUEST decision says NOT_REQUIRED. PB-002 always requires independent approval. |
| AC-PLY-09 | A request has an immutable REQUEST-stage decision before approval; approval references `request_id` and that decision; after approval/preconditions, a separate immutable EXECUTION-stage ALLOW decision is created; execution has its own `id`/`tenant_id` and references both decisions. |
| AC-PLY-10 | Each execution action attempt is durably persisted with tenant, sequence, idempotency key, connector/action/target, evidence, status and outcome. |
| AC-PLY-11 | Request, both policy-decision stages, approval, execution and grant foreign keys enforce the conditional approval relationship; `approval_id` is nullable only when the REQUEST-stage decision says it is not required; cross-tenant references fail under constraints and RLS. |
| AC-EXEC-MODE-01 | Every tenant/domain/connector-action scope defaults independently to SHADOW when no exact-scope setting exists; no wildcard or broader-scope LIVE inheritance is applied. |
| AC-EXEC-MODE-02 | The execution-mode API requires the exact domain-pack, connector and canonical action-capability identifiers; omitted, wildcard or ambiguous scope requests are rejected. |
| AC-EXEC-MODE-03 | Switching one exact scope to LIVE leaves every other scope unchanged; tests prove independent state for internal merchant correction and PayPal `refunds.create`. |
| AC-EXEC-MODE-04 | A grant is denied unless the exact action scope is LIVE, even if another action scope is LIVE and the request is approved. With the scope LIVE, execution is still denied when required independent approval is absent, the approved intent changed or an execution-time precondition fails. Switching a scope to LIVE re-evaluates the requests that scope blocked. |

## 16.6 PayPal and extensibility criteria

| ID | Acceptance criterion |
|---|---|
| AC-PP-01 | Sandbox adapter authenticates and retrieves supported transaction/capture details. |
| AC-PP-02 | Required financial report data is retrievable/importable for reconciliation. |
| AC-PP-03 | Invalid signature is rejected before tenant derivation; no tenant `raw_evidence`/`webhook_inbox` row or canonical financial state is changed. Forensics use only a separate platform security ingress log. |
| AC-PP-04 | Duplicate webhook delivery is deduplicated safely. |
| AC-PP-05 | `PAYMENT.CAPTURE.COMPLETED` creates/updates provider evidence and triggers reconciliation. |
| AC-PP-06 | Completed PayPal capture plus pending merchant payment creates `STATUS_MISMATCH`. |
| AC-PP-07 | PB-001 corrects merchant state via authorized action and verifies resulting record. |
| AC-PP-08 | Refund demo requires duplicate-capture proof and approval; resulting refund is verified completed. |
| AC-PP-09 | Transaction Search is not treated as real-time; webhook/report paths are separate. |
| AC-PP-10 | Integration Admin can securely register PayPal credential references and webhook configuration; Business Admin inheritance alone cannot perform those operations. |
| AC-PP-11 | Balance Reconciliation Report CSV ingestion is implemented and tested as P0; only automated SFTP/report retrieval is P1. |
| AC-PP-12 | PayPal adapter publishes only canonical capability keys (`transactions.read`, `captures.read`, `refunds.read`, `refunds.create`, `webhooks.receive`, `reports.balance_reconciliation.read`, `files.upload`) or explicitly registered versioned aliases. |
| AC-PP-13 | A secret reference resolves only within the tenant of the source that holds it: a reference that resolves for Tenant A is refused for Tenant B at storage and fails closed at use, and a deployment-wide environment secret is not resolvable as a source secret in production. |
| AC-PP-14 | Write-only credential intake stores a tenant's provider credentials in the secret manager under its namespace; the value is never returned, logged, audited or held in an ordinary database field; rotation stops the old value being used. Required before a tenant other than the SYNTHETIC demonstration tenant connects a provider account. |
| AC-MERCH-01 | Success path: with the exact scope LIVE, a valid grant and the persisted status `PENDING`, `merchant.payment_status.correct` changes exactly one tenant-scoped payment row to `COMPLETED`; the post-action readback through `merchant.payment.read` returns `COMPLETED` before the case resolves. |
| AC-MERCH-02 | Stale state: when the persisted status differs from `expected_current_status` (for example already `COMPLETED`, or changed concurrently), the action fails closed with no mutation; the attempt is recorded with a stale-state classification and the case returns to `HUMAN_REVIEW`. |
| AC-MERCH-03 | Target resolution: a tenant/merchant/payment identifier that matches zero rows, more than one row or a row belonging to another tenant fails closed with no mutation in any tenant; the cross-tenant case returns no information about the other tenant's row. |
| AC-MERCH-04 | Transition allowlist: any `requested_status`/`expected_current_status` pair other than `PENDING -> COMPLETED` is rejected by action-schema validation before any database statement is issued. |
| AC-MERCH-05 | Idempotency: repeating the action with the same idempotency key and identical payload returns the original durable outcome and performs exactly one mutation; reusing the key with a different payload is rejected with no mutation. |
| AC-MERCH-06 | No arbitrary SQL: the manifest and action schema accept only the declared fields; requests carrying unknown fields, SQL text, table or column names are rejected at validation. A contract test proves no caller-supplied value is interpolated into SQL, and the adapter's database role can execute only the declared readback query and conditional status update. |
| AC-MERCH-07 | Capability keys: the internal-merchant adapter publishes only `merchant.payment.read` and `merchant.payment_status.correct`; switching its action scope to LIVE does not make PayPal `refunds.create` or any other scope LIVE. |
| AC-OPEN-01 | Source registry accepts non-payment connector and stores its declared capabilities without source-model changes. |
| AC-OPEN-02 | Generic file/API source ingests without amount, currency or payment status requirements. |
| AC-OPEN-03 | Tenant can activate two compatible packs concurrently with separate namespaces/versions. |
| AC-OPEN-04 | Pack registers entities, validators, case taxonomy, tools and playbooks without global enum/core connector changes. |
| AC-OPEN-05 | Finance projection can be created while unrelated records remain preserved for other packs. |
| AC-OPEN-06 | Connector lacking webhook/action capability remains usable for supported batch-read; unsupported action is unavailable. |
| AC-OPEN-07 | Non-financial reference pack loads, validates, activates and runs its fixture without platform-core contract changes. |

## 16.7 Test layers and release blockers

| Test layer | Focus |
|---|---|
| Unit | Generic envelope, numeric calculations, invariant functions, identity candidate generation, hard match constraints/cardinality, workflow transitions, idempotency and state machines |
| Contract | Connector capability and payload contracts for API/database/file/event sources; route-permission declarations; API validation; webhook signature handling |
| Integration | PostgreSQL/RLS; source adapters; evidence storage; ontology validation/publication; domain engines; Agent tools; policy gateway; action worker and verification |
| Security | Cross-tenant IDOR, tenant context, invitation replay, stale permission, role/set name anti-pattern, prompt injection, secrets, break-glass and permission-scope bypass |
| End-to-end | Full onboarding including Integration Admin, PayPal payment recovery, duplicate capture refund, request/decision/approval/execution chain, SHADOW denial, LIVE-in-Sandbox execution, second tenant/domain pack and isolation |
| Failure injection | Duplicate/out-of-order webhook, invalid signature, source-to-tenant bootstrap failure, queue retry, network timeout with ambiguous action outcome, a scope returned to SHADOW between decision and grant, concurrent publication/owner transfer and database restart |

Release rule: if any safety-critical test involving tenant isolation, decimal precision, evidence preservation, authorization, owner invariant, publication hash, idempotency or post-action verification fails or is skipped, the affected domain-pack/connector-action scope MUST NOT be switched to LIVE for a demonstration or release. The product may still be demonstrated in SHADOW mode.

---

# 17. MVP implementation plan and delivery gates

The ten-day goal is a bounded demonstration, not a production claim. The proposed work allocation is a planning heuristic: ontology/semantic studio 25%; PayPal and source ingestion 25%; deterministic reconciliation 20%; AI/playbooks 20%; multi-tenant/security/deployment 10%. Rebalance from measured risks; do not reduce security gates to preserve feature percentages.

| Gate | Outcome | Required evidence / exit criteria |
|---|---|---|
| G1 - Identity and foundation | Registration, verification, tenant creation, owner pointer, invitation flow, permission catalogue, generic source schema, RLS baseline, audit | IAM and tenant tests; permission map for every route/tool; no seeded demo identities |
| G2 - Semantics | Source discovery/profile, ontology composition/draft, multi-source mapping/observations, authority editor, SHACL + pack validators, publish confirmation | Exact-hash publication tests; immutable version/pinning; two sources to one property fixture |
| G3 - Finance correctness | PayPal/merchant/bank canonical data, identity resolution, both reconciliation scopes, settlement calculations, case taxonomy | Deterministic fixtures, precision and semantic-comparability tests; no unresolved matches forced |
| G4 - Provider lifecycle | PayPal Sandbox API access, webhook signature/dedupe, Transaction Search backfill, report CSV upload | Webhook/replay/ordering tests; verified source data; account/API access checked |
| G5 - Safe investigation and action | Durable investigation/tool trace; Operator request; immutable policy decision; independent approval when required; tenant-scoped execution/action attempts; single-use Worker grant; all-domain SHADOW default; independent verification | AC-INV-01; AC-PLY-08..11; AC-AUTHZ-06/08/15/17; AC-EXEC-MODE-01..04; AC-MERCH-01..07; POL-01/06/09 |
| G6 - Extensibility proof | SaaS reference pack, both packs in one tenant, and the deployed demo business (§18) | AC-OPEN-01..07; finance logic remains pack-scoped. Cross-tenant isolation is verified by AC-TEN-01..03 at G1 and G3, not by a separate G6 test (CHG-1.8-01) |

**Build order:** permission catalogue and route guard must be created at the foundation, not retrofitted later. Every API route and every agent tool declares permission from the outset. Freeze the API schema, connector manifest and canonical source envelope before parallel frontend/backend work. Do not demonstrate automatic action until all applicable safety tests pass.

---

# 18. Primary demonstration script

The demo runs SettleIQ in a controlled test deployment and PayPal in Sandbox. “LIVE” below refers only to SettleIQ's scoped execution mode; the provider environment remains Sandbox.

The demonstration subject is a separately deployed demo business: a SaaS company with its own website, database and PayPal Sandbox app, connected to SettleIQ only through the supported connectors (PayPal, `merchant-postgres` and `internal-merchant`). Discrepancies arise from faulty procedures on the business's own checkout, not from records injected into SettleIQ. The business's website alone shows what each of its sources says and offers a reset that returns its sources to their discrepant state under fresh identifiers, so judges can run the script again. SettleIQ keeps every earlier case, execution and audit record; nothing in SettleIQ is reset. PayPal captures and refunds cannot be undone, so PayPal-side discrepancies return through a new Sandbox checkout. Judges use team-provided identities for the Tenant Owner, Domain Operator and Approver roles, and return a scope to SHADOW before rerunning steps 10 to 15.

1. Register a real team-controlled email identity, verify it and create Tenant A. Show the creator becomes the sole initial Tenant Owner.
2. As Owner, invite distinct Integration Admin, Domain Operator and Approver email identities. Each invitee accepts from their own verified mailbox. Show the resulting memberships and data-defined permission sets.
3. Create the PayPal source record. As Integration Admin, add the PayPal Sandbox credential reference and register the webhook. Upload bank CSV and P0 Balance Reconciliation Report CSV. Show credential values never appear in the UI or logs. If the SYNTHETIC/REFERENCE BRR fixture is used (§10.3.1), show its SYNTHETIC/REFERENCE label, fixture version and checksum in the UI, and state on screen and in the demo video narration that settlement results use reference data, not a report generated by the team's PayPal account.
4. Discover/profile source metadata; generate the platform-core + Financial Operations ontology draft; review source mappings and fact-specific authority; run SHACL and every active pack validator; explicitly publish the exact validated hash.
5. On the demo business, buy a plan through PayPal Sandbox checkout with the lost-confirmation fault switched on: PayPal captures the payment and sends a verified `PAYMENT.CAPTURE.COMPLETED` event, while the business's payment row stays `PENDING`. Show both on the business's source view.
6. As Domain Operator, trigger the domain run/reconciliation. Show `STATUS_MISMATCH`, the pinned source/ontology/mapping/rule versions and source evidence.
7. Run the AI investigation. Show evidence references, recommendation and `requires_approval` output. Confirm the Agent did not create a request, approval or execution record.
8. As Domain Operator, create a PB-001 request. For this demo, the immutable REQUEST-stage policy decision is `approval_required=true`. Show `playbook_requests` and the REQUEST-stage `policy_decisions` row exist before any approval or execution row.
9. Attempt to approve the same request as its requester (where test grants overlap) and show POL-01 denies it. Then sign in as the separate Approver and approve the request. Show `playbook_approvals.request_id` references the request.
10. Demonstrate SHADOW denial regardless of approval. Use a Worker/service integration-test harness to request a grant while SHADOW is active; show POL-06 denies issuance and no business-state mutation occurs. Do not invoke the private Worker route from the browser.
11. Show that the approved request is POLICY_BLOCKED while the `internal-merchant` / `merchant.payment_status.correct` scope is SHADOW, and that no grant was issued.
12. As Tenant Owner, switch only `(Tenant A, Financial Operations, internal-merchant adapter, merchant.payment_status.correct)` to SettleIQ LIVE and show the mode transition in audit. The PayPal connector remains Sandbox; no PayPal refund permission is implied by this switch.
13. After approval and precondition revalidation, create an immutable EXECUTION-stage policy decision with outcome `ALLOW`; create the tenant-scoped execution row; then issue a one-time Worker grant bound to both policy decisions, request, approved approval ID, execution, action/target and idempotency key. Worker replays the internal merchant event; SettleIQ re-reads merchant state and verifies `COMPLETED` before resolving the case.
14. Demonstrate a controlled duplicate-capture refund: with the double-charge fault switched on, a checkout retry produces two distinct successful capture IDs for one intended order; prove the duplicate and absence of prior refund/hold; create request; obtain independent Approver approval. As Tenant Owner, separately switch only `(Tenant A, Financial Operations, PayPal Sandbox adapter, refunds.create)` to SettleIQ LIVE and audit the transition. Confirm that the PB-001 merchant-correction scope setting is unchanged and that the PayPal provider environment remains Sandbox.
15. Re-evaluate PB-002 preconditions and approval; create its EXECUTION-stage `ALLOW` decision and tenant-scoped execution; issue the one-time grant bound to the exact `refunds.create` action and target. Execute through PayPal Sandbox; retrieve refund state and verify completion.
16. Show complete audit lineage: invitation/grants, source registration/credential reference changes, webhook receipt/signature verification, ontology publication hash, domain run, investigation/tool trace, request/policy decision, approval, both independent execution-mode transitions, execution grant, action attempt and post-action verification.
17. In the same tenant, with the SaaS Subscription Lifecycle reference pack active beside Financial Operations, run the SaaS pack over the business's subscription and invoice records and show the RENEWAL_INVOICE_MISMATCH case. Then reset the demo business from its website and show that the discrepancies return for a second run.

The demo MUST NOT show a successful mutation while SHADOW is active. The source discussion's estimate that PayPal may represent 35-40% of the technical demonstration is a presentation-weight estimate, not a code or engineering-effort allocation.


---

# 19. Risk register

| Risk | Failure mode | Required mitigation / evidence |
|---|---|---|
| Incorrect ontology semantics | Incorrect domain mapping and downstream decisions | Evidence-backed proposals, human edit/review, SHACL + pack validation, immutable versioning |
| Incorrect source authority | Wrong system value treated as truth | Fact-specific authority, retain observations, conflict/unresolved state, no silent recency override |
| Bad entity resolution | Unrelated records merged or same entity split | Candidate evidence, hard constraints, ambiguous review, false-link evaluation |
| Incomparable fields | Original total compared with fulfilled value or mismatched currency/time | Explicit semantic property, unit/currency/time comparability checks |
| Settlement double counting | Incorrect expected net | Report accounting semantics and included-in-net flags; deterministic fixture tests |
| LLM hallucination/prompt injection | False root cause, leakage or unapproved action | Least privilege, evidence references, structured outputs, no execution tools, tool validation |
| Duplicate external action | Duplicate refund or correction | Durable idempotency, single-use grant, ambiguous-timeout read-before-retry |
| Cross-tenant access | Data or audit leakage | Server-derived tenant context, RLS, scope checks, negative IDOR/tool tests |
| Permission drift | UI and backend disagree or route left unprotected | Central catalogue, route/tool registry, CI map and test generation, deny-by-default |
| Owner race / last owner lockout | Zero/multiple owners or orphaned tenant | Owner pointer, row lock, same-transaction transfer, last-holder constraints and concurrency tests |
| Invitation replay or misdelivery | Unauthorized membership | Hashed single-use token, verified-email match, expiry/revocation, audit |
| Ontology stale validation | Publish unvalidated content | Hash-bound validation and confirmation, edit invalidation, atomic immutable snapshot |
| PayPal latency/availability | Stale/missing provider state | Webhooks + backfill/report separation, retries and explicit freshness metadata |
| Webhook disorder | Out-of-order or repeated events mutate state incorrectly | Inbox, signature verification, event-ID dedupe, event-time handling, idempotent downstream processing |
| Overbuilt infrastructure | Delivery delay without correctness benefit | PostgreSQL-first, defer graph DB/Kafka/RDF store until measured need |
| Incomplete playbook | Unsafe or unverifiable action | Explicit trigger/preconditions/approval/action/idempotency/verification/failure contract; PB-003 non-executable |
| Request/execution lifecycle collapse | POL-01 lacks requester identity or tenant RLS cannot be enforced | Separate request, policy decision, approval, execution, grant and action-attempt tables; request-first FK chain and schema tests |
| Missing integration capability in demo | PayPal credentials/webhooks cannot be configured by invited members; merchant write action or BRR input unavailable | Invite Integration Admin explicitly and test credential/webhook permissions end to end; require the merchant adapter manifest, the exact-scope mode and readback; use the versioned synthetic BRR fixture if the provider report is unavailable |
| Shadow bypass | Approved action executes while SHADOW, or a scope is switched LIVE with failed safety tests | POL-06 blocks every business-state mutation and is checked at the execution stage, at grant issue and by the Worker; the §16.7 release rule keeps a scope SHADOW until its safety-critical tests pass |
| Untested build switched LIVE | A Tenant Owner switches a scope to LIVE on a build whose safety-critical tests did not pass | CI fails on any safety-critical test failure (§16.7) and deploys only from a green pipeline; with POL-14 retired (CHG-1.8-01), no runtime check binds LIVE to a tested build |
| Webhook tenant confusion | Provider event persisted in wrong tenant or bypasses RLS | Verify signature, resolve trusted source registration, derive tenant context transaction-locally, then use RLS |
| Investigation trace loss | Agent result/tool usage cannot be reproduced or audited | Durable tenant-scoped `investigations` and append-only `investigation_tool_calls` |
| Tenant enumeration | Error response confirms an inaccessible tenant exists | Equivalent external 404 for absent and non-member tenant/resource; internal reason only in protected audit |
| Break-glass privilege misuse | Platform support obtains tenant evidence without independent review | Separate platform security approver, self-approval prohibition, time-bound scope, audit and auto-expiry |

---

# 20. Architecture decisions frozen for V1

| ADR | Decision |
|---|---|
| ADR-001 | Platform core remains domain-neutral; Financial Operations is an independently versioned pack. |
| ADR-002 | Connectors and domain packs are orthogonal, capability-declaring/versioned extension points. |
| ADR-003 | Generic canonical source envelope preserves arbitrary payload attributes; typed domain projections are pack-scoped. |
| ADR-004 | Raw evidence is preserved separately and immutably with hash/provenance. |
| ADR-005 | PostgreSQL/JSONB is initial operational and semantic registry store; JSON-LD exchange; SHACL/pySHACL structure validation. |
| ADR-006 | Do not introduce graph DB, Kafka or separate RDF triple store until measured workload justifies it. |
| ADR-007 | Multiple source mappings target one semantic property via separate binding entries and append-only property observations. |
| ADR-008 | Source identity resolution is separate from both mapping and reconciliation. |
| ADR-009 | Intra-entity observation conflict and inter-entity invariant are distinct finding scopes. |
| ADR-010 | Fact-specific source authority selects a resolved view but never deletes observations; unresolved conflict is explicit. |
| ADR-011 | Domain calculations are deterministic and type-safe; financial money is decimal-safe. |
| ADR-012 | Single AI orchestrator is read/recommend/validate only with narrowly bounded append-only investigation/tool-trace writes and proposed classification metadata; it cannot mutate business state, case lifecycle, request, approve, reject or execute. |
| ADR-013 | Human Operator requests; human Approver decides; deterministic Worker acts only with one-time execution grant. |
| ADR-014 | Playbook execution is durable, idempotent, policy-gated and independently verified. |
| ADR-015 | SHADOW is default and blocks all business-state changes in every domain; LIVE switch is Tenant Owner only. SettleIQ LIVE is independent of provider Sandbox/production environment. |
| ADR-016 | Authorization checks permission keys; permission sets are data; non-capability constraints are policy rules. |
| ADR-017 | Owner assignment is represented only by `tenants.owner_user_id`; owner transfer is atomic and audited. |
| ADR-018 | Ontology publication requires exact-hash validation, explicit `confirm_publish=true`, immutable snapshot and audit; V1 has no two-person publication gate. |
| ADR-019 | Global user identity with tenant-local memberships and email invitation flow; no pre-seeded demo identities. |
| ADR-020 | PayPal is the first Finance adapter; webhook event intake and report/backfill are separate paths; Balance Reconciliation Report CSV ingestion is P0 and automated SFTP/report retrieval is P1. |
| ADR-021 | Requests, policy decisions, approvals, executions, execution grants and action attempts are distinct durable tenant-scoped records. Approval references request; execution grant binds policy decision and conditionally approval. |
| ADR-022 | Case lifecycle, playbook-request lifecycle and execution lifecycle are separate state machines; only verified business outcome resolves a case. |
| ADR-023 | Retired in v1.8 (CHG-1.8-01). State-changing execution needs no release attestation; the exact-scope mode is checked at the execution stage, at grant issue and by the Worker. |
| ADR-024 | PayPal webhook tenant context is derived from trusted source registration only after signature verification and before RLS-protected persistence. |
| ADR-025 | Investigation persistence is durable and append-only; Agent writes are limited to result/tool trace/proposed classification metadata. |
| ADR-026 | Break-glass support requires two distinct platform principals: requester and independent security approver; support access is scoped/read-only by default and expires. |
| ADR-027 | Retired in v1.8 (CHG-1.8-01). Capability-level certification is not used; reintroducing any release attestation requires a further revision. |

---

# 21. Open decisions and implementation questions

The following are unresolved decisions. OPEN identifiers are reserved for this section; Section 1.6 uses EXT-REQ identifiers for normative extensibility requirements. Assign owners and close each item before its dependent implementation gate.

| ID | Open item | Required resolution point |
|---|---|---|
| OPEN-01 | Detailed connector manifest schema, protocol/capability negotiation and adapter lifecycle | Before G1 adapter implementation |
| OPEN-02 | Source adapter roadmap and which connectors beyond the MVP set are required | Product roadmap |
| OPEN-03 | Domain-pack manifest, compatibility/conflict resolution, migration/rollback guarantees | Before G2 pack activation |
| OPEN-04 | Canonical envelope schema evolution, optional metadata and event/file conventions | Before G1 ingestion contract freeze |
| OPEN-05 | Multi-pack semantic collisions, overrides and property ownership precedence | Before supporting multiple production packs |
| OPEN-06 | Entity-resolution blocking, candidate features, score calibration and false-link targets | Before production matching thresholds |
| OPEN-07 | Currency scale, rounding, FX and decimal precision beyond selected MVP currencies | Before financial production use |
| OPEN-08 | Authority assertion conflict policy and valid-time semantics | Before production multi-source authority |
| OPEN-09 | Observation retention, corrections, compaction and resolved-view refresh strategy | Before production scale |
| OPEN-10 | Exact provider settlement/report accounting treatment and included-in-net semantics | Before enabling settlement actions |
| OPEN-11 | Playbook definition DSL, compensation, retry/backoff, dead-letter and manual recovery contracts | Before authoring beyond MVP playbooks |
| OPEN-12 | PB-003 executable scope, approvals, action contract and verification | Must remain non-executable until closed |
| OPEN-13 | Webhook replay/backfill ownership, event ordering and recovery from provider outages | Before production webhook operation |
| OPEN-14 | API schema conventions, auth/session transport, pagination, idempotency header, versioning and error envelope | Before frontend/backend integration |
| OPEN-15 | Quantified production SLOs, throughput, latency, availability, RTO/RPO and backup policy | Before production deployment |
| OPEN-16 | Raw evidence retention, deletion, privacy and legal-hold policy | Before production data onboarding |
| OPEN-21-17 | Grant condition language beyond V1 domain-pack scope and `max_amount` for `playbook.approve` | Before P1 custom conditions |
| OPEN-21-18 | Whether Approver receives `audit.read` or only execution-scoped history | Before expanding audit UI; V1 Approver has `execution.read` only |
| OPEN-21-19 | Permission cache lifetime and invalidation design if permissions are cached | Before production auth implementation; revocation must take effect next decision |
| OPEN-21-20 | Whether SHADOW-to-LIVE switch needs second-holder confirmation | Before enabling LIVE beyond tightly scoped MVP demo |
| OPEN-21-21 | Default PB-001 approval policy and risk threshold for non-demo tenants; the request must not choose it. V1 demo is fixed to REQUIRED. | Before enabling PB-001 NOT_REQUIRED in any tenant |
| OPEN-PLAT-01 | Provisioning, identity assurance, rotation and revocation lifecycle for `platform_security_approver` principals; tenant admins cannot manage them. | Before enabling break-glass support access |
| OPEN-REL-02 | Retired in v1.8 (CHG-1.8-01), with POL-14. | None |
| OPEN-CAP-01 | Final published connector capability manifest schema and compatibility alias policy; canonical V1 keys are defined in §4.4/§10.3. | Before connector contract freeze |
| OPEN-PP-01 | Current PayPal account/app partner requirements, scopes, rate limits and Sandbox availability | Verify against current official provider documentation before adapter build |
| OPEN-PP-02 | PayPal partner/multiparty onboarding, in which the tenant grants SettleIQ delegated access and shares no app secret; it needs PayPal partner approval. V1 uses tenant-owned apps under §10.1. | Before onboarding tenants whose security review refuses to share app credentials |
| OPEN-PP-03 | Runtime write path for the write-only credential intake (§11.3): a narrowly scoped secret-manager write credential for the API, limited to tenant source paths, or a different secret backend | Before AC-PP-14 is implemented |
| OPEN-ONT-01 | Whether future ontology publication needs a two-person approval policy | Deferred; V1 uses authenticated explicit confirmation |

Decided in this v1.8 baseline: POL-14 release attestation is removed (CHG-1.8-01), which also retires OPEN-REL-02. From v1.7: tenants connect their own PayPal apps (§10.1), secret references are tenant-namespaced (§11.3), and partner onboarding is deferred as OPEN-PP-02. From v1.6: OPEN-REL-01 was closed by a capability-level attestation contract, now retired. From v1.5: v1.5 adds acceptance criteria AC-MERCH-01..07 for the internal-merchant adapter, names its readback capability `merchant.payment.read`, and requires synthetic-BRR disclosure in the demo. From v1.4: the v1.3 decisions on stage-specific policy IDs, the separate EXT-REQ namespace, complete-replacement authority, the `ACTION_PENDING` trigger, invalid-webhook isolation and endpoint 404 behavior remain in force. The v1.5 baseline additionally defines the internal-merchant write adapter, the BRR fallback, ordered onboarding and execution workflows, UI/backend permission parity and the exact merchant action scope. Historical v1.0–v1.4 files are retained for traceability only and are not parallel implementation specifications.

---

# 22. Traceability and change control

## 22.1 Traceability

| Requirement family | Primary sections in v1.8 | Historical lineage |
|---|---|---|
| Product intent, extensibility and MVP scope | 1-2 | v0.4 baseline |
| Architecture, connector and pack contracts | 4 | v0.4 and v0.5 |
| Ontology, mapping, source authority and publication | 5 | v0.4, v0.7, v0.8 |
| Source data, storage and schema | 6-7 | v0.4/v0.5 |
| Entity resolution and intra/inter reconciliation | 7 | v0.4 semantic-binding amendment |
| AI investigation | 8 | v0.4 and v0.8 |
| Playbooks, approvals, execution and shadow mode | 9 | v0.4, v0.7, v0.8 |
| Exact-scope execution mode without release attestation | 9.5, 9.7, 16.5 | v1.6, removed in v1.8 |
| Tenant provider-account connection and tenant-namespaced secrets | 10.1, 11.3, 12.2, 14.2, 16.6 | v1.7 |
| PayPal adapter, webhook and report handling | 10 | v0.5 |
| Identity, invitation and tenant membership | 3, 11, 14 | v0.6 |
| Permission sets and policy gateway | 3, 11-12 | v0.7 |
| Owner pointer and transfer | 3, 11-12 | v0.8 |
| Frontend, workflows and security | 11-14 | v0.4-v0.8 |
| NFR, tests, delivery, demo and DoD | 15-18, 23 | v0.4-v0.8 |

## 22.2 Change control

This v1.8 document is the complete and authoritative V1 implementation baseline and, on approval, fully replaces v1.7 and every earlier specification. Any requirement change MUST be incorporated into a new complete, integrated revision. Each revision MUST restate all operative requirements, including unchanged requirements, in its own sections; no prior revision or sidecar amendment is needed or normative. Record each change with a change ID, affected requirement/ADR, rationale, impact analysis, compatibility/migration plan, test updates, approver and effective version.

## 22.3 Implementation status convention

Track each acceptance criterion as `NOT STARTED`, `IN PROGRESS`, `BLOCKED` or `VERIFIED`, with a test/run artifact and responsible owner. Do not mark any criterion VERIFIED solely because a design decision exists or a manual happy-path demo worked. Authorization and release-blocking criteria require automated tests. For V1, a criterion traced to automated tests takes its status from those tests in CI; a separate status sheet is not required.

## 22.4 Revision record

| Change ID | Version | Affected requirements | Change and rationale | Test impact |
|---|---|---|---|---|
| CHG-1.5-01 | 1.5 | §4.4.1, §16.6, §17 G5, §23 item 19 | Added AC-MERCH-01..07 so every MUST in the internal-merchant adapter contract has a test; PB-001 is the primary demo and previously had only a happy-path criterion (AC-PP-07). | New contract, integration and security tests; G5 exit criteria extended. |
| CHG-1.5-02 | 1.5 | §4.4, §4.4.1 | Named the adapter readback capability `merchant.payment.read` and listed both merchant keys as canonical, so precondition checks and post-action verification are declared, testable capabilities. | AC-MERCH-01 and AC-MERCH-07 verify the key. |
| CHG-1.5-03 | 1.5 | §18 step 3 | Required visible and narrated disclosure when the SYNTHETIC/REFERENCE BRR fixture is used, aligning the demo script with §10.3.1. | Demo checklist only. |
| CHG-1.6-01 | 1.6 | POL-14 and fail-closed semantics, §4.1, ARCH-10, §4.4, §6.3, §9.5, new §9.7, §16.2, §16.7, §17 G5, §19, NFR-024, ADR-023, new ADR-027, §23 item 21 and release safety rule, Appendix B | Moved POL-14 from build-level to capability-level, image-bound certification, so a signature cannot imply more than its evidence proves. | New AC-REL-01..12; AC-AUTHZ-16 tightened; skipped tests now block issuance. |
| CHG-1.6-02 | 1.6 | §21, §22.1 | Closed OPEN-REL-01 through §9.7 and opened OPEN-REL-02, so release-level attestation and production hardening are explicit decisions rather than implied ones. | None beyond CHG-1.6-01. |
| CHG-1.7-01 | 1.7 | §10.1, §11.3, §12.2, §14.2, §16.6, §21 | Each tenant connects its own PayPal REST app with write-only credential intake, instead of one team-controlled app; partner onboarding is deferred (OPEN-PP-02). A multi-tenant product cannot act on tenants' accounts through one team app, and partner approval is outside the V1 timeline. | New AC-PP-14; OPEN-PP-03 decides its write path. |
| CHG-1.7-02 | 1.7 | §11.3, ARCH-03, §16.6 | Secret references resolve within the holding source's tenant only, and deployment-wide environment secrets are refused in production. Under v1.6 a reference was a deployment-wide name, so one tenant's Integration Admin could name another tenant's or the platform's secret. | New AC-PP-13. |
| CHG-1.9-01 | 1.9 | §14.4, §17 G6, §18 (preface and steps 5, 14, 17), §22.3, §23 item 28 | The demonstration becomes a separately deployed demo business that judges run and reset themselves, with both packs active in one tenant. The SaaS pack is no longer required to run in a second tenant without Financial Operations; that case stays proven by automated tests. Criterion status in V1 comes from the automated trace tests rather than a separate status sheet. | No requirement or acceptance criterion is removed. AC-OPEN-03 and AC-OPEN-07 are traced to the SaaS pack tests. |
| CHG-1.8-01 | 1.8 | POL-03, POL-06, POL-11, POL-14 and its fail-closed semantics, §3.1, §3.3, §4.1, ARCH-10, §4.4.1, §6.3, §8.4, §9.5, §9.7, §10.6, §11.5, §12.2, NFR-024, §16.2, §16.5, §16.6, §16.7, §17 G5 and G6, §18, §19, ADR-015, ADR-023, ADR-027, OPEN-REL-02, §22.1, §23 items 21 and 29 and release safety rule, Appendix A, Appendix B | Removed POL-14 release attestation: no developer-side issuance, signing, verification or approval gates execution. A tenant's own controls remain: exact-scope SHADOW by default, the Tenant Owner's LIVE switch, the REQUEST decision, independent approval (POL-01), execution-stage revalidation, single-use grants and readback. Also drops the Tenant B isolation check from the G6 exit. | AC-REL-01..12 and AC-AUTHZ-16 retired; AC-EXEC-MODE-04 absorbs AC-REL-11; AC-MERCH-07 restated in terms of mode. |

### 22.4.1 Change record for v1.6

| Field | CHG-1.6-01 and CHG-1.6-02 |
|---|---|
| Impact analysis | An attestation that would pass under v1.5 can fail under v1.6, because more bindings are checked. Adds a capability-manifest generator, an image-based test harness, signed revocation states and a runtime-identity service. Tenant scoping, Appendix A permission keys, the request-to-execution chain, SHADOW and read-only behavior do not change. |
| Compatibility and migration | No attestation has been issued and neither attestation table exists yet, so nothing migrates. `release_gate_attestations` is built to the §6.3 definition, and `release_revocation_states` is new. |
| Test updates | AC-REL-01..12, AC-AUTHZ-16, the §16.7 release rule and §23 item 21. |
| Approver | Rishav Chowdhury, 09 November 2026. The approval covers the integrated v1.6 text, including the reviewer rule in §9.7.4. |
| Effective version | 1.6, effective 09 November 2026. |

### 22.4.2 Change record for v1.7

| Field | CHG-1.7-01 and CHG-1.7-02 |
|---|---|
| Impact analysis | A reference that resolved under v1.6 fails closed under v1.7 unless its secret sits under the holding tenant's namespace, and `env:` references stop resolving in production. Adds a write-only intake route and a runtime secret-store write path (OPEN-PP-03) before tenants other than the demonstration tenant connect. Permission keys, the request-to-execution chain, SHADOW, POL-14 and webhook verification do not change. |
| Compatibility and migration | Secret values move from the flat deployment path `<param path>/source/<NAME>` to `<param path>/source/<tenant_id>/<NAME>`; stored references are unchanged and need no database migration. Local development and CI keep `env:` references. |
| Test updates | AC-PP-13 (tenant namespacing) and AC-PP-14 (write-only intake). AC-PP-10 is unchanged. |
| Approver | Swapnaneel Ray, 11 November 2026. The approval covers the integrated v1.7 text. |
| Effective version | 1.7, effective 11 November 2026. |

### 22.4.3 Change record for v1.8

| Field | CHG-1.8-01 |
|---|---|
| Impact analysis | A scope can now go LIVE, and a grant can be issued, without any release attestation, so LIVE is no longer bound at runtime to a tested build. The safety-critical tests remain release-blocking through the §16.7 release rule, applied by CI and by whoever switches a scope to LIVE. The request-to-execution chain, POL-01, POL-06, POL-09, SHADOW by default, the exact-scope key, grants, the Worker and readback do not change. `platform.release.attest` leaves Appendix A, and the `release_attestor` principal is removed. |
| Compatibility and migration | Migration 0020 drops `release_gate_attestations`, `release_revocation_states`, the revocation high-water mark and `platform_service_principals`, the `attestation_id` columns on `execution_mode_settings`, `policy_decisions` and `execution_grants`, and the check that a LIVE row carries an attestation; the grant guard is replaced to match. No attestation was ever issued, so no data is lost. The attestation workflow, signer, runtime-identity service and write-once bucket are deleted. |
| Test updates | AC-REL-01..12 and AC-AUTHZ-16 are retired with their tests. Mode tests switch scopes to LIVE without an attestation, and a schema check and a code scan prove no release attestation remains. AC-EXEC-MODE-04 and AC-MERCH-07 are traced to mode tests. |
| Approver | Rishav Chowdhury, 12 November 2026. The approval covers the integrated v1.8 text. |
| Effective version | 1.8, effective 12 November 2026. |

### 22.4.4 Change record for v1.9

| Field | CHG-1.9-01 |
|---|---|
| Impact analysis | Only the demonstration and the extensibility proof change. Platform core, the authorization model, the execution chain, POL-01, POL-06, POL-09, SHADOW by default, exact scopes, grants, the Worker and readback do not change. The SaaS pack adds no connector action, so it adds no execution-mode scope. The demo business is outside SettleIQ: it reaches SettleIQ only through the supported connectors and the PayPal webhook, and its reset never touches SettleIQ data. |
| Compatibility and migration | No migration. The SaaS pack ships as data and pack code under `packs/saas_subscription`; no platform-core contract changes (AC-OPEN-07). |
| Test updates | The SaaS pack tests cover the pack alone and beside Financial Operations in one tenant. The demo business has its own CI. Tenant isolation remains covered by AC-TEN-01..03. |
| Approver | Pending. |
| Effective version | 1.9, effective on approval. |

---

# 23. Definition of done for the MVP

The MVP is demonstrable only when all of the following are true:

1. A tenant can register a connector-neutral source configuration whose type is not hardcoded to PayPal, bank or ERP.
2. A non-financial source record can be preserved in the generic canonical envelope without coercion into a Finance record.
3. Tenant ontology composition stores platform core, selected domain pack(s) and tenant extensions as separate versioned artifacts.
4. Finance-specific entities, settlement formulas, discrepancy types and PayPal workflows exist under the Financial Operations pack, not as platform-wide enums/mandatory fields.
5. A user can create two isolated tenant workspaces through real identity flows.
6. Tenant A connects PayPal Sandbox, merchant PostgreSQL read plus the separate internal-merchant write adapter, bank CSV and the P0 BRR CSV through generic source registry contracts. If the provider BRR is unavailable, the versioned, checksummed SYNTHETIC/REFERENCE fixture is used for deterministic tests only.
7. SettleIQ discovers/profiles source metadata and generates a draft ontology; users can edit relationships, source bindings and authority.
8. Multiple source fields can bind to one comparable semantic property through separate versioned mappings and distinct observations.
9. Source records link to canonical entities only through traceable identity decisions; ambiguous candidates remain reviewable.
10. Resolved views expose applicable authoritative value while retaining non-authoritative observations and explicit conflicts.
11. Both intra-entity observation discrepancy and inter-entity invariant fixtures generate correctly scoped findings.
12. SHACL and every active pack validator run before publication; any failure blocks publication; exact-hash validation, explicit confirmation and immutable audit-backed snapshot are enforced.
13. Source records retain raw evidence and provenance and project to Finance only where applicable.
14. Finance engine deterministically detects primary `STATUS_MISMATCH` and settlement fixtures with decimal-safe math; BRR included-in-net treatment prevents double counting.
15. Agent investigates tenant-scoped evidence and recommends a structured playbook. It cannot create requests, approve, reject or execute; its writes are limited to append-only investigation result/tool traces and proposed classification metadata.
16. Human Operator creates a durable playbook request; the policy engine stores an immutable policy decision before approval; an independent Approver decides when required.
17. Request, decision, approval, execution, execution grant and action attempts are separate durable tenant-scoped records with correct foreign keys and RLS. Approval references the request, not a future execution.
18. Every execution grant references both `request_policy_decision_id` (provenance) and `execution_policy_decision_id` (the immutable EXECUTION-stage `ALLOW` authorizing the action); the approval reference is mandatory iff the REQUEST-stage policy requires it, otherwise NULL. Grants are one-time, scoped and consumed atomically with Worker start.
19. Playbook execution is idempotent, independently verified and audited. API success alone never resolves a case. The internal-merchant adapter passes AC-MERCH-01..07, including stale-state, ambiguous-target, cross-tenant, idempotency and no-arbitrary-SQL tests.
20. SHADOW mode prevents every business-state-changing action across all domains, including internal merchant correction, even if a request is approved.
21. Every state-changing action scope is SHADOW until the Tenant Owner switches that exact scope to LIVE; the mode is checked again at the execution stage, at grant issue and by the Worker, and AC-EXEC-MODE-01..04 pass.
22. SettleIQ LIVE mode is explicitly distinguished from provider environment; the primary demo runs LIVE execution policy against PayPal Sandbox.
23. The PayPal webhook route verifies signature, derives tenant context from trusted source registration, sets transaction-local tenant context and persists through RLS; payload-supplied tenant ID is ignored.
24. Case lifecycle supports reasoned dismiss/close/reopen and cannot resolve on failed/ambiguous/unverified action.
25. Permission map covers every protected route/tool; role/set names are absent from authorization code; Integration Admin, Operator and Approver permissions are demonstrated using real invited identities.
26. External tenant/resource error responses do not reveal whether an inaccessible tenant exists; detailed reasons remain protected audit data.
27. Break-glass request requires an independent platform security approver; requester cannot self-approve; access is scoped/read-only by default, audited and expires.
28. A tenant can activate the non-financial SaaS reference pack alone or beside Financial Operations and run its subscription/invoice exception fixture; each pack's run reads only its own classes.
29. Ownership, publication, invitation, tenant isolation, money precision, evidence preservation, permission enforcement, idempotency, execution mode and post-action verification criteria pass automated tests.

**Release safety rule:** if any critical criterion involving tenant isolation, money precision, evidence preservation, authorization, owner invariant, publication validation, idempotency, execution mode or outcome verification fails or is skipped, the affected domain-pack/connector-action scope MUST NOT be switched to LIVE. SHADOW mode may still be used for a controlled demonstration.

---

## Appendix A. Core permission catalogue (V1 baseline)

This is the platform key set implied by the V1 routes and default permission sets. Permission keys are registered data and route/tool metadata. Packs may add non-colliding namespaced keys through a validated manifest.

| Namespace / keys | Purpose |
|---|---|
| `platform.tenant.read`, `platform.tenant.manage`, `platform.connector.register`, `platform.domain_pack.register`, `platform.ops.read`, `platform.support.request`, `platform.support.approve`, `platform.support.access` | Platform operations; support access only through POL-10. `platform.release.attest` was retired in v1.8 (CHG-1.8-01) |
| `tenant.read`, `tenant.update`, `tenant.delete`, `tenant.ownership.transfer` | Tenant lifecycle and ownership |
| `member.read`, `member.invite`, `member.invitation.manage`, `member.role.assign`, `member.remove`, `role.read`, `role.manage` (P1), `audit.read` | Membership, grant management and audit |
| `connector.read`, `source.read`, `source.create`, `source.update`, `source.delete`, `source.credential.manage`, `source.discover`, `source.sync`, `source.file.upload`, `source.webhook.manage` | Source/connector administration |
| `source_record.read`, `evidence.raw.read`, `connector.action.invoke` | Source evidence and connector actions |
| `domain_pack.read`, `domain_pack.manage` | Domain-pack lifecycle |
| `ontology.read`, `ontology.generate`, `ontology.edit`, `ontology.validate`, `ontology.publish` | Semantic lifecycle and publication |
| `mapping.read`, `mapping.edit`, `mapping.publish`, `authority.read`, `authority.edit`, `rule.read`, `rule.edit`, `policy.read`, `policy.edit` | Mapping, source authority, invariants and policy |
| `domain_run.read`, `domain_run.create`, `entity.read`, `entity_resolution.read`, `entity_resolution.review` | Domain runs, entities and identity review |
| `case.read`, `case.triage`, `case.investigate`, `case.classify`, `investigation.read`, `investigation.record`, `domain.compute` | Case management; Agent classification is proposed metadata only and investigations/traces are append-only |
| `playbook.read`, `playbook.edit` (P1), `playbook.publish` (P1), `playbook.recommend`, `playbook.validate`, `playbook.request`, `playbook.approve`, `playbook.reject`, `playbook.execute` | Human request/decision and controlled Worker execution |
| `execution.read`, `execution.record`, `outcome.verify`, `execution_mode.manage` | Execution, verification and execution mode |

## Appendix B. Glossary

| Term | Definition |
|---|---|
| Canonical source record | Generic normalized representation of one source record, retaining arbitrary attributes and provenance |
| Domain entity | Canonical semantic entity representing a business object across one or more source records |
| Source binding | Versioned mapping from external source object/field to semantic class/property |
| Property observation | Immutable, provenance-bearing value observed from a source for one semantic entity/property |
| Resolved property view | Derived value/status computed from eligible observations and fact-specific authority |
| Entity resolution | Process that decides which domain entity a source record represents |
| Intra-entity reconciliation | Comparison of equivalent observations about one entity/property |
| Inter-entity reconciliation | Validation of relationship/invariant across distinct entities |
| Domain pack | Versioned domain vocabulary, validators, matching policies, cases, agent tools and playbooks |
| Permission set | Data-defined named bundle of permissions; not an authorization code branch |
| Execution grant | One-use Worker grant binding `execution_policy_decision_id` (EXECUTION-stage ALLOW) and `request_policy_decision_id` (provenance); `approval_id` iff required |
| Playbook request | Durable human-created request for a specific immutable playbook version, created before policy decision, approval or execution |
| Policy decision | Immutable stage-tagged record: REQUEST captures the approval requirement and request outcome; EXECUTION records final action authorization and MUST be ALLOW for grant issuance |
| Case lifecycle | State machine for business exception handling, distinct from request and execution states |
| Shadow mode | Process/recommendation mode in which no business-state-changing action in any domain is executable |

## Appendix C. Source assumptions requiring implementation verification

This baseline intentionally distinguishes requirements from source-discussion assumptions. In particular, PayPal Transaction Search freshness/latency, webhook retry guarantees, partner-network/multiparty access, provider rate limits and exact account entitlements must be checked against current official provider documentation and the actual Sandbox app/account before production implementation. The product architecture must not depend on an unverified provider guarantee.

