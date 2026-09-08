# ADR-0012 — Per-market immutable catalog modules with signed optional delivery

**Status:** Accepted  
**Date:** 2026-09-08

## Context

ADR-0002 established an immutable bundled SQLite runtime. ADR-0007 measured Germany at 53,205 products and 73.25 MiB vacuumed / 14.27 MiB gzip, while the deterministic 5x runtime model reaches 332.23 MiB and crosses the current 250 MiB bundled-catalog planning threshold. ADR-0007 therefore made Germany the initial partition, rejected a monolithic multi-market database by default, and required independent market/source-rights review.

Issue #72 needs an EU-scale architecture that lets a user install Germany alone or several markets while retaining fully offline lookup. The same GTIN may represent different formulations, evidence and assessments in different markets; retailer evidence is also many-to-many with products. Physical partitioning by retailer would duplicate product/formulation data and make cross-retailer evidence semantics harder to preserve.

The existing app composition injects `ProductCatalog` and `ProductSearchCatalog` once into long-lived feature models. Existing favorites/history are barcode-scoped because the accepted runtime is Germany-only; multi-market resolution requires an explicit market dimension. Issue #25 separately owns the cryptographic/publication mechanics for optional GitHub Release delivery.

## Decision

Adopt **one immutable SQLite catalog per market/country** as the default physical scale unit and preserve the bundled Germany database as a complete functional fallback.

### Runtime routing

A concurrency-safe market catalog router owns the active market and a set of verified read-only catalog/search repositories. It implements or fronts the existing domain repository boundaries so scanner and search features continue to depend on domain protocols rather than SQLite, files, networking, or release metadata.

The user selects the active market explicitly. Device/App Store region may suggest a market but does not choose it silently and precise location is never requested. Exact lookup/search use the active market only. Missing/unsupported markets are explicit recoverable states; the router never falls through to Germany or another market and thereby changes formulation semantics.

Each accepted scan, manual lookup, or search operation captures an immutable market context before asynchronous work begins. If Settings changes the active market while that operation is in flight, the old operation is cancelled/invalidated or its stale result is discarded; the same physical/user event is never silently retargeted to a different market. Product-detail presentation and any result/not-found evidence-submission draft inherit the exact market/catalog identity that produced the result. A later market switch does not rewrite an existing submission draft.

Saved history/favorites become `(market, GTIN)` references. The writable local store moves to a market-aware schema; legacy Germany-only records may be deterministically migrated to `DE`. This remains local mutable user state and is not part of any downloaded module.

### Module artifact boundary

A market release contains:

1. the deterministic production SQLite for one market;
2. its existing production catalog manifest;
3. a canonical market-module manifest that binds market/release/app compatibility, database and manifest digests/bytes, source/license/coverage identity, signing key ID, and rollback/supersession metadata;
4. redistribution/attribution material required for the public asset; and
5. a detached asymmetric signature under the #25 trust design.

The inner catalog manifest remains the authority for SQLite semantic/schema/integrity identity. The outer module manifest is a delivery/market/trust envelope; it does not duplicate or reinterpret product evidence.

Downloaded files are untrusted until the full envelope and existing production catalog validation pass. Candidates live in bounded temporary storage and are atomically moved into a versioned application-support module directory only after verification. Modules are then opened immutable/read-only/query-only. The previous verified module stays available for rollback until replacement is proven.

Full market SQLite assets are used first. Binary/row deltas are rejected for initial implementation because current evidence does not justify the extra patch-verification/rollback complexity.

### Distribution and availability

GitHub Release assets are the initial distribution channel because the repository is public and no custom backend/account is required. Network availability is never on the lookup path. Outage, rate limiting, invalid release metadata, insufficient storage, verification failure, or source-rights withdrawal must leave the last verified module/bundled baseline operable.

Module availability is separate from product/retailer completeness. Coverage/freshness data is published as qualified manifest/UI metadata using the accepted health/coverage semantics; it cannot create stronger evidence or a completeness claim.

## Security and privacy consequences

- No module request includes scans, searches, history/favorites, OCR/submission data, location, accounts, device IDs, analytics IDs, or stable user identifiers.
- Release bytes are public/mirrorable and therefore may contain only redistribution-safe runtime data and attribution.
- Only reviewed protected-main production outputs may be signed/published.
- Signature verification supplements HTTPS and is required before activation; mutable tags/URLs are not trust anchors.
- The app keeps a bundled baseline and previous verified module for rollback.
- Module files never contain user data and user-library files never become module inputs.

## Alternatives rejected

### One monolithic EU SQLite

Rejected as the default because measured 5x growth already exceeds the current 250 MiB planning threshold, market/source rights may differ, and users should not need unrelated markets. A later benchmark may revisit packaging but cannot weaken market identity isolation.

### One SQLite per retailer

Rejected because retailers are evidence dimensions, the same GTIN/formulation appears at several retailers, and retailer partitioning would duplicate product/ingredient/assessment semantics.

### Network-first lookup/backend

Rejected because it breaks the core offline/privacy/reliability contract and is unnecessary for indexed SQLite lookup.

### Runtime merge/copy of modules into one writable catalog

Rejected initially. It adds write/migration/atomicity complexity and blurs physical source/license partitions without demonstrated lookup benefit. Multiple verified immutable modules plus an active-market router is simpler and fail-closed.

### Binary/row deltas first

Rejected until real update-bandwidth measurements justify patch complexity. Full compressed market assets have a simpler signature/digest/rollback proof.

## Consequences and follow-up

- Specification 029 is the normative runtime/UX contract.
- Specification 027 is extended so Settings may persist market selection/module-management state in addition to appearance and may display/manage locally verified module identity.
- Specification 006/008 is extended narrowly so saved history/favorites include market code.
- Specification 018/020 is extended so result presentation and evidence-submission context use the exact active market/catalog identity rather than assuming the bundled Germany catalog.
- Specification 010/012/028 must distinguish the mandatory bundled app release from optional post-install signed market assets.
- Issue #25 implements the signing/channel/publication and atomic download/install mechanics under this architecture.
- Issue #26 admits additional markets only after their source, methodology, localization, coverage, size, and release gates pass.
- GitHub-hosted push/PR validation remains mandatory and can prove behavior with deterministic production-shaped multi-market fixtures before real product breadth exists.
