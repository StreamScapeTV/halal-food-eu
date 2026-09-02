# 025 — Source-aware catalog refresh

Status: **Accepted**

## Purpose

Keep catalog evidence current without turning partial acquisition, source outages, stale cursors, or unchanged snapshots into false freshness or false deletions. Refresh behavior is source-specific, deterministic, auditable, bounded by each admitted source contract, and integrated with the existing catalog-quality and catalog-health authorities.

This specification extends 005 (data sourcing), 010 (catalog releases), 014 (evidence exchange), 016 (workflow orchestration), 023 (certificate validity), and 024 (catalog health). It does not weaken any source-admission, licensing, provenance, assessment, or release requirement in those specifications.

For generated-branch confinement only, REF-004 and REF-012 explicitly supersede the receipt-only branch rule in HF-WORKFLOW-020 after the existing receipt-only catalog proposal writer has created or reused the deterministic reviewed branch. The refresh-promotion companion may additionally update only `Data/refresh/accepted-open-food-facts-v1.json` and `Data/refresh/accepted-open-prices-v1.json` on that same branch, and only for changed candidates bound to source snapshots present in the exact reviewed aggregate evidence. No raw source material, generated SQLite/manifest, product image, unrelated metadata, or other repository path is admitted by this supersession.

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

The scheduler and manual dispatcher must resolve to the same deterministic acquisition/refresh plan for equivalent source, mode, source state, and evaluation time.

### REF-003 — Durable accepted lineage and operational cadence

The durable source state is the versioned/attested accepted catalog lineage, never an Actions cache. Operational acquisition facts may additionally be retained in immutable workflow/health artifacts and, when a material catalog proposal is reviewed, in the accepted source-state metadata.

Per source, refresh state must distinguish:

- the latest attempted acquisition represented by the current refresh artifact;
- the last accepted complete source state used as catalog/evidence authority;
- an eligible complete candidate waiting for protected-branch acceptance;
- the latest successful complete **full acquisition** time/snapshot used only for acquisition scheduling;
- upstream revision metadata such as ETag, Last-Modified, snapshot revision, or cursor when officially supported;
- content digest and record count;
- adapter and source-policy versions;
- full versus delta mode and predecessor/cursor lineage when applicable; and
- the next due full-acquisition reason/time derived from the latest successful complete full acquisition when known.

The operational acquisition clock and accepted evidence clock are independent. A successful complete full acquisition may advance `lastSuccessfulFullAcquisitionAt` / `nextFullDueAt` even when its bytes are unchanged or its changed candidate is not yet accepted. That operational advancement must not rewrite `acceptedComplete.retrievedAt`, ingredient `observedAt`, retailer observation dates, certificate dates, assessment dates, or any other evidence-freshness field.

If no successful complete full acquisition is known, the full lane remains explicitly due rather than fabricating a successful baseline. Delta acquisitions do not reset the full-acquisition cadence unless a future reviewed source policy explicitly defines equivalent complete semantics.

### REF-004 — Candidate promotion

A complete passing acquisition may become `candidateComplete`. It must not silently replace `acceptedComplete` in the same unreviewed acquisition step.

Promotion to accepted state is allowed only when the candidate is included in the exact reviewed catalog proposal/accepted protected-branch lineage. Promotion must be deterministic: the promoted accepted record is byte-for-byte the candidate record, the candidate slot is cleared, and the resulting state digest is recomputed canonically.

For a multi-source reviewed catalog proposal, each admitted source candidate is evaluated independently against its protected accepted lineage and its exact source snapshot in the reviewed aggregate evidence. Changed candidates may be promoted together on the same deterministic catalog proposal branch; an unchanged source candidate remains a no-op and must not block another source's reviewed material catalog change. The resulting branch may differ from protected `main` only at the release receipt plus the fixed accepted-state paths for sources whose candidates actually changed.

A logical catalog no-op must not create a noisy catalog-data proposal merely to record transient attempt or operational cadence metadata. Health/reporting artifacts may retain that operational evidence separately. Consequently, a successful unchanged source check can schedule the next acquisition without creating a catalog PR and without freshening accepted evidence.

### REF-005 — Partial and failed acquisition

A partial, sampled, truncated, failed, policy-blocked, schema-blocked, or quality-blocked acquisition must never replace a prior accepted complete source state or advance the successful-full-acquisition clock.

For such runs:

- old evidence must not receive a new `retrievedAt` or equivalent freshness timestamp;
- the operational `lastSuccessfulFullAcquisitionAt` / `nextFullDueAt` remain anchored to the prior successful complete full acquisition;
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

A validated not-modified/unchanged complete response may advance the operational acquisition clock while preserving accepted content and evidence observation clocks. It must not fabricate a new content observation.

### REF-008 — Independent evidence clocks

Ingredient formulation, retailer observation, certification, assessment evidence, and source acquisition cadence remain independently dated.

A retailer listing refresh must not refresh ingredient age. A certificate recheck must not refresh formulation age. A source acquisition success must not refresh an unchanged formulation merely because the transport was checked again. A source outage must not refresh any old observation.

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

Owner-admitted submission targets may be derived only from the committed non-personal evidence boundary defined by 019. Raw email packages, package photos, local scan history, not-found history, reviewer identity, and other per-user data must never be copied into refresh queues. When no admitted submission or reviewed aggregate demand signal exists, the queue must not invent one.

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
- latest successful full acquisition/next due full acquisition state;
- deletion-reconciliation eligibility;
- queue entry count and reason counts; and
- stable refresh blocker/deduplication keys.

Scheduled workflow health must remain source-specific even when multiple sources share one workflow file: a newer successful run for one source must not hide a failed run for another source.

Stale queues and scheduled refresh failures requiring action must therefore remain visible through the trusted catalog-health workflow rather than only in ephemeral job logs.

### REF-012 — Proposal and release behavior

Normalization/diff may feed build/proposal only after the required complete-input and quality gates for that lane have passed. The refresh artifacts are explicit workflow handoffs and must remain bound to the same source/snapshot/producer lineage.

A logical catalog no-op must create no catalog PR. Repeated generation of the same queue/report for identical inputs must be idempotent. A blocking refresh/source/quality condition creates or updates aggregate health evidence instead of publishing a new catalog.

After a catalog proposal merges, post-merge validation must confirm the accepted source state and catalog lineage against protected `main` before release/TestFlight packaging. Production release must fail closed if an accepted-state file is malformed, contains an unpromoted candidate, or retains candidate eligibility/change flags.

### REF-013 — Future retailer and manufacturer sources

Future official Lidl, REWE, manufacturer, certifier, GS1, or other source adapters must register their own source access method, permissions, cadence, pagination/cursor semantics, completeness contract, legal/redistribution terms, and rate limits before any refresh execution.

An official retailer source may provide listing/availability evidence without providing ingredient authority. The refresh system must preserve that distinction and must not copy retailer freshness onto formulation evidence.

### REF-014 — Recovery and schedule inactivity

Manual recovery must work with GitHub-hosted runners and reviewed public/default-branch workflow code; private infrastructure is not required for the credential-free public-source recovery path.

Because public-repository schedules may be disabled after prolonged inactivity, operator documentation and health output must identify the relevant manual workflow and the next expected cadence so missed schedules are diagnosable.

## Acceptance tests

The implementation must cover, at minimum:

1. schedule/manual plan equivalence;
2. same-snapshot and same-content/new-run idempotency;
3. conditional-header planning for supported metadata and omission for unsupported metadata;
4. valid delta predecessor and cursor-expiry/missing-cursor fallback to full;
5. partial acquisition preserving the prior accepted complete state, prior operational-success clock, and forbidding false deletion;
6. source outage/no false freshness;
7. successful unchanged acquisition advancing only operational cadence, not accepted/evidence freshness;
8. formulation change routing and certificate invalidation/recheck routing;
9. independent formulation/retailer/certificate/acquisition clocks;
10. bounded rate plan and retry metadata;
11. source-policy/schema/auth blocker behavior;
12. queue and catalog-proposal deduplication;
13. health projection of source-specific refresh failures/stale queues;
14. admitted submission targeting without user identity/history leakage;
15. paired-source promotion with independent changed/unchanged candidates and fixed branch-path confinement;
16. protected-main accepted-state validation before production release/TestFlight packaging;
17. no secrets or user identities in pull-request/fork/queue surfaces; and
18. manual recovery/default-branch schedule safeguards.
