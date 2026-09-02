# 025 — Source-aware catalog refresh

Status: **Accepted**

## Purpose

Keep catalog evidence current without turning partial acquisition, source outages, stale cursors, or unchanged snapshots into false freshness or false deletions. Refresh behavior is source-specific, deterministic, auditable, bounded by each admitted source contract, and integrated with the existing catalog-quality and catalog-health authorities.

This specification extends 005 (data sourcing), 010 (catalog releases), 014 (evidence exchange), 016 (workflow orchestration), 023 (certificate validity), and 024 (catalog health). It does not weaken any source-admission, licensing, provenance, assessment, or release requirement in those specifications.

## Requirements

### REF-001 — Source-specific cadence

Each refreshable source must have reviewed source-controlled refresh policy declaring, at minimum:

- source key and adapter version;
- permitted acquisition modes;
- full-refresh cadence;
- targeted-refresh cadence when targeted access is admitted;
- bounded request rate, batch size, and per-run target count when network targeting is admitted;
- conditional metadata supported by that source, if any; and
- the source-policy version used to authorize acquisition.

A global convenient cron must never create permission to call an endpoint. If a source contract does not admit a targeted API, the targeted planner may emit work but network execution for that source must remain disabled.

### REF-002 — Trusted schedules and manual equivalence

Scheduled refresh workflows must run only from reviewed protected `main` workflow code and must also expose `workflow_dispatch` for manual recovery. Scheduled jobs must use off-hour minute offsets rather than top-of-hour schedules.

For the initial public-source set:

- Open Food Facts receives a weekly complete bulk snapshot;
- Open Prices receives a weekly complete snapshot on an independently staggered schedule; and
- the daily targeted lane may execute only against sources whose reviewed source policy explicitly admits the required targeted endpoint and rate limits.

The scheduler and manual dispatcher must resolve to the same deterministic acquisition/refresh plan for equivalent source, mode, accepted state, and evaluation time.

### REF-003 — Durable accepted lineage

The durable source state is the versioned/attested accepted catalog lineage, never an Actions cache.

Per source, refresh state must distinguish:

- the latest attempted acquisition represented by the current refresh artifact;
- the last accepted complete source state;
- an eligible complete candidate waiting for protected-branch acceptance;
- upstream revision metadata such as ETag, Last-Modified, snapshot revision, or cursor when officially supported;
- content digest and record count;
- adapter and source-policy versions;
- full versus delta mode and predecessor/cursor lineage when applicable; and
- the next due refresh reason/time derived from the accepted complete state.

Operational attempts that are not accepted may be retained in workflow/health evidence without becoming catalog authority.

### REF-004 — Candidate promotion

A complete passing acquisition may become `candidateComplete`. It must not silently replace `acceptedComplete` in the same unreviewed acquisition step.

Promotion to accepted state is allowed only when the candidate is included in the exact reviewed catalog proposal/accepted protected-branch lineage. Promotion must be deterministic: the promoted accepted record is byte-for-byte the candidate record, the candidate slot is cleared, and the resulting state digest is recomputed canonically.

A logical catalog no-op must not create a noisy catalog-data proposal merely to record transient attempt timestamps. Health/reporting may retain that operational evidence separately.

### REF-005 — Partial and failed acquisition

A partial, sampled, truncated, failed, policy-blocked, schema-blocked, or quality-blocked acquisition must never replace a prior accepted complete source state.

For such runs:

- old evidence must not receive a new `retrievedAt` or equivalent freshness timestamp;
- a missing record must not be interpreted as deletion;
- deletion reconciliation must be disabled;
- the accepted complete digest/revision remains unchanged; and
- a stable refresh/health blocker is emitted when operator action or retry is required.

If no accepted complete state exists, a partial run remains non-authoritative rather than becoming an inferred baseline.

### REF-006 — Full and delta selection

A source may use delta acquisition only when its reviewed source policy explicitly admits delta mode and the accepted state contains the exact valid predecessor/cursor required by that source.

If the cursor is missing, expired, incompatible with the adapter/source-policy version, or rejected by the source, the next safe plan is a complete snapshot. The workflow must not infer a replacement cursor or infer deletions from an incomplete delta chain.

Current Open Food Facts and Open Prices production policy remains complete-snapshot based until an explicit reviewed delta contract is added.

### REF-007 — Conditional requests

Conditional requests may be planned only for metadata explicitly declared by both the refresh policy and the admitted source behavior. A request planner may project `If-None-Match` from an accepted ETag and `If-Modified-Since` from an accepted Last-Modified value only when the relevant source contract admits those semantics.

For a source without reviewed conditional support, those headers must be omitted even if a prior response happened to contain similarly named metadata.

A validated not-modified response preserves accepted content and may update operational health evidence, but must not fabricate a new content observation.

### REF-008 — Independent evidence clocks

Ingredient formulation, retailer observation, certification, and assessment evidence remain independently dated.

A retailer listing refresh must not refresh ingredient age. A certificate recheck must not refresh formulation age. A source outage must not refresh any old observation.

A changed formulation must invalidate or route the affected prior assessment for mandatory re-review according to 013/014. Expired, revoked, suspended, or materially changed certificate evidence must invalidate or re-route certification-dependent assessment according to 023. The refresh queue must surface these conditions without silently assigning a new halal status.

### REF-009 — Deterministic work queues

Refresh processing must emit a bounded deterministic machine-readable queue. Supported reasons include, as applicable:

- missing current ingredients;
- unknown ingredient observation date;
- stale ingredients;
- changed-unreviewed formulations;
- ambiguous/high-risk assessment;
- assessment recheck due;
- source/identity conflict;
- certificate recheck due;
- certificate expired/revoked/suspended;
- source or quality blocker;
- admitted not-found/new-submission target; and
- privacy-safe demand target when such an aggregate signal is available.

Queue deduplication must use stable source/market/GTIN/reason identity. Queue ordering and truncation must be deterministic. Queue artifacts must not contain user identities, email addresses, raw scan histories, or other per-user activity.

### REF-010 — Targeted execution safety

For an admitted targeted source, execution must:

- validate every target as a bounded GTIN/market tuple before network access;
- use only the reviewed HTTPS host/path contract;
- use the required project User-Agent/contact configuration;
- honor the stricter of configured and documented rate limits;
- honor `Retry-After` for retryable responses;
- use bounded retries/backoff and bounded response size/record counts; and
- record exact target/result metadata needed for deterministic normalization and review.

A plan that is safe to generate is not automatically safe to execute. `networkExecutionPerformed=false` is required when the source is not admitted for targeted execution.

### REF-011 — Health integration

Catalog health (024) must expose refresh health in the same aggregate report/incident system. At minimum it must project:

- source key;
- attempt status;
- accepted complete snapshot identifier and retrieval time, when present;
- candidate-changed state;
- next due refresh time/reason;
- deletion-reconciliation eligibility;
- queue entry count and reason counts; and
- stable refresh blocker/deduplication keys.

Stale queues and scheduled refresh failures requiring action must therefore remain visible through the trusted catalog-health workflow rather than only in ephemeral job logs.

### REF-012 — Proposal and release behavior

Normalization/diff may feed build/proposal only after the required complete-input and quality gates for that lane have passed. The refresh artifacts are explicit workflow handoffs and must remain bound to the same source/snapshot/producer lineage.

A logical catalog no-op must create no catalog PR. Repeated generation of the same queue/report for identical inputs must be idempotent. A blocking refresh/source/quality condition creates or updates aggregate health evidence instead of publishing a new catalog.

After a catalog proposal merges, post-merge validation must confirm the accepted source state and catalog lineage against protected `main` before release/TestFlight packaging.

### REF-013 — Future retailer and manufacturer sources

Future official Lidl, REWE, manufacturer, certifier, GS1, or other source adapters must register their own source access method, permissions, cadence, pagination/cursor semantics, completeness contract, legal/redistribution terms, and rate limits before any refresh execution.

An official retailer source may provide listing/availability evidence without providing ingredient authority. The refresh system must preserve that distinction and must not copy retailer freshness onto formulation evidence.

### REF-014 — Recovery and schedule inactivity

Manual recovery must work with GitHub-hosted runners and reviewed public/default-branch workflow code; private infrastructure is not required for the credential-free public-source recovery path.

Because public-repository schedules may be disabled after prolonged inactivity, operator documentation and health output must identify the relevant manual workflow and the next expected cadence so missed schedules are diagnosable.

## Acceptance tests

The implementation must cover, at minimum:

1. schedule/manual plan equivalence;
2. same-snapshot idempotency;
3. conditional-header planning for supported metadata and omission for unsupported metadata;
4. valid delta predecessor and cursor-expiry/missing-cursor fallback to full;
5. partial acquisition preserving the prior accepted complete state and forbidding false deletion;
6. source outage/no false freshness;
7. formulation change routing and certificate invalidation/recheck routing;
8. independent formulation/retailer/certificate clocks;
9. bounded rate plan and retry metadata;
10. source-policy/schema/auth blocker behavior;
11. queue and catalog-proposal deduplication;
12. health projection of refresh failures/stale queues;
13. no secrets or user identities in pull-request/fork/queue surfaces; and
14. manual recovery/default-branch schedule safeguards.
