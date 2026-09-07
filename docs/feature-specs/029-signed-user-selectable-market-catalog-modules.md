# 029 — Signed user-selectable market catalog modules

**Status:** Accepted  
**Last reviewed:** 2026-09-07

## Purpose

Halal Food EU may supplement its immutable bundled Germany catalog with optional, user-selected, signed market/country catalog modules. The feature preserves a complete bundled offline fallback, exact market-specific formulation and assessment semantics, source/license boundaries, local-only scan behavior, and deterministic GitHub-hosted validation.

This specification promotes full-market catalog modules from future roadmap scope. It does not authorize per-retailer databases, network lookup on the scan path, unsigned catalog replacement, cross-market formulation inheritance, or publication of source data whose redistribution rights do not permit public module delivery.

## Baseline and partitioning

- **HF-MODULE-001:** The application continues to ship a complete functional bundled `catalog.sqlite3` + `catalog-manifest.json` baseline. Network access, GitHub availability, downloaded modules, signing credentials, or a user account are never required for the app to launch and perform the baseline offline lookup/search/evidence flow.
- **HF-MODULE-002:** The default downloadable unit is one immutable SQLite runtime catalog per ISO 3166-1 alpha-2 market. Retailers remain evidence/filter dimensions inside a market module; the app does not create one physical database per retailer by default.
- **HF-MODULE-003:** A market module contains only the admitted compact runtime projection for that market: exact product identity, current market-specific ingredient/evidence projection, assessments/reasons/certifications, retailer evidence, compact exclusions, deterministic search indexes, source/license/attribution identity, and required catalog metadata. Raw acquisition payloads, broad audit history, private/restricted material, submission/user data, and image binaries are excluded.
- **HF-MODULE-004:** The same GTIN in two markets is two independently resolved market records. A product, ingredient observation, assessment, certification, retailer claim, freshness state, or saved-product comparison from one market must never be inherited into another market merely because GTIN/name/brand matches.
- **HF-MODULE-005:** The app may keep multiple verified market modules installed simultaneously. Every module is opened read-only/query-only through the same production catalog repository boundary; runtime never migrates or writes a downloaded catalog.

## Market selection and routing

- **HF-MODULE-006:** The user explicitly chooses the active market and which supported market modules are installed. App Store/device region may be used only as a first-run suggestion; precise location is not requested or used for market selection.
- **HF-MODULE-007:** Scanner exact lookup, offline product search, product detail, and not-found semantics use only the explicitly active market. For `DE`, the last verified downloaded Germany module may supersede the bundled Germany catalog, otherwise the bundle is the deliberate Germany fallback. For any other active market, a missing/removed/unverified module produces a recoverable unsupported/not-installed state; the app must not silently answer from Germany or another installed market.
- **HF-MODULE-008:** Switching the active market does not recreate the top-level app shell. A long-lived concurrency-safe market catalog router may change the selected read-only catalog beneath the existing domain repository/use-case boundaries so scanner/search features do not acquire SQLite/download concerns.
- **HF-MODULE-009:** Settings is the user-visible surface for market selection/module state and shows market name/code, installed catalog version/date, approximate installed/download size where known, ingredient/evidence coverage limitations, update result, and removal/reset actions. No new top-level tab is added.
- **HF-MODULE-010:** Market selection is local non-sensitive preference state. This requirement supersedes HF-SETTINGS-002 only to permit persisted selected/installed market identifiers and related local module-management state in addition to appearance; it does not authorize analytics, location history, account identity, or scan-derived preference data.

## Saved history and favorites

- **HF-MODULE-011:** A saved history/favorite reference is market-scoped. The writable user-library store persists the ISO market code that was active at the physical scan/favorite event together with the existing GTIN/time/catalog-version/fingerprint fields. This narrowly extends HF-HISTORY-003 and HF-PRIVACY-008; no image, ingredient text, remote image reference, location, or full product record is added.
- **HF-MODULE-012:** Opening a history/favorite item resolves the saved `(market, GTIN)` against the current verified catalog for that same market. If the market module is unavailable, the UI reports that state and must not resolve against the currently active different market.
- **HF-MODULE-013:** The existing local user-library schema may perform one explicit fail-closed migration to add market scope to pre-module records. Legacy records created before multi-market support are assigned `DE` only because the accepted pre-module application has a Germany-only catalog contract; malformed/unknown local schema still fails closed without affecting product lookup.

## Signed release envelope

- **HF-MODULE-014:** A downloadable market release is an immutable versioned set containing the SQLite database, the existing production catalog manifest for that exact database, a canonical market-module manifest, required redistribution/attribution material, and a detached asymmetric signature. GitHub provenance/attestation may supplement but never replaces in-app signature verification.
- **HF-MODULE-015:** The module manifest binds at minimum: module schema/version ID, market code, catalog/schema/methodology versions, app compatibility range, database byte count and SHA-256, inner catalog-manifest SHA-256, attribution/license bundle digest, quality/coverage/freshness summary, source snapshot/release identity, compressed/installed bytes, publication timestamp distinct from evidence dates, signing key ID, and supersedes/rollback identity.
- **HF-MODULE-016:** Before activation the app verifies the signed release policy, key ID/trust state, detached signature, database/manifest/license digests and byte bounds, exact market, schema/application IDs, app compatibility, source-policy identity, SQLite integrity, and the existing production catalog compatibility checks. A failure at any step rejects the candidate before it becomes queryable.
- **HF-MODULE-017:** Downloads use an isolated temporary location with bounded redirects/hosts/bytes/time and are atomically promoted only after full verification. Downloaded module files are reproducible public data and are excluded from device cloud backup. The previous verified module is retained until replacement succeeds. Crash, cancellation, truncated/tampered data, GitHub outage, rate limiting, low storage, or incompatible metadata leaves the last verified module/bundled baseline usable.
- **HF-MODULE-018:** Full versioned market SQLite assets are the initial update unit. Binary/row deltas remain future work until measurements prove material benefit and a separate accepted design preserves canonical verification/rollback semantics.

## Privacy and networking

- **HF-MODULE-019:** Module metadata/download requests contain no scanned GTIN, search query, history/favorite data, OCR/submission data, account/device identifier, advertising identifier, location, analytics identifier, or stable project-generated user identifier. Core barcode lookup/search remains offline after installation.
- **HF-MODULE-020:** Module networking is optional, user-visible, and isolated from product classification. An unavailable update service cannot change a halal result, freshness date, evidence record, or current verified catalog bytes.
- **HF-MODULE-021:** User removal/reset deletes only downloaded module files and module-management metadata. It never deletes favorites/history implicitly. Removing the active `DE` download falls back to the bundled Germany catalog; removing another active market leaves that market explicitly unavailable until reinstalled or the user selects a different market. Saved entries for a removed market remain local.

## Coverage and evidence semantics

- **HF-MODULE-022:** “Market module available” is not a retailer/product completeness claim. Manifest/UI coverage derives from accepted #21/#64 metrics and must distinguish discovered-corpus coverage from any official-defined denominator. Retailer completeness remains forbidden without the retailer-specific denominator/reconciliation gate.
- **HF-MODULE-023:** A partial market module may be published when its admitted evidence is useful and passes all quality/safety gates, but limitations are shown explicitly. Synthetic fixtures may prove software behavior and isolation but never substitute for real production evidence or redistribution rights.

## Release and CI

- **HF-MODULE-024:** Only a reviewed protected-`main` production workflow may sign/publish a market module. It reuses the product-owned deterministic catalog compiler and all applicable evidence/quality/security/query-plan/iOS compatibility gates, publishes immutable GitHub Release assets initially, re-downloads the public assets, and independently verifies signature/digests/content before marking that module release usable.
- **HF-MODULE-025:** Ordinary push and pull-request validation remains fully available on GitHub-hosted runners without Central CI, release credentials, GitHub Release publication, or network product data. Deterministic production-shaped two-market fixtures exercise the complete application seam.
- **HF-MODULE-026:** Central Apple validation remains optional. TestFlight continues to package the accepted bundled production baseline; optional downloaded market modules are post-install data and do not become a hidden TestFlight prerequisite.

## Performance and storage

- **HF-MODULE-027:** Each market module must independently satisfy the accepted indexed lookup/query-plan/schema/integrity requirements. The active-market warm exact lookup remains within HF-PERF-001; switching/opening a module is measured separately and may not perform whole-catalog parsing on `@MainActor`.
- **HF-MODULE-028:** Before a market rolls out, release evidence records installed/compressed size, lookup/open performance, temporary+rollback disk requirement, and source-license compatibility. Growth that crosses accepted device/app/module budgets requires architecture review rather than silently merging markets into a monolith.

## Acceptance

Automated and release validation must cover at minimum:

- two deterministic markets containing the same GTIN with intentionally different formulation/assessment, proving no cross-market inheritance;
- active-market scanner and search routing, unsupported/not-installed market behavior, and market switching;
- history/favorite market scoping plus v1→v2 local-store migration and removed-module behavior;
- valid/tampered/wrong-market/wrong-key/wrong-digest/incompatible-schema/incompatible-app/oversized/truncated release candidates;
- atomic install, replacement, cancellation/crash recovery, rollback, removal, and bundled reset/fallback;
- offline launch/lookup with GitHub unavailable;
- no scan/history/search/user identifiers in module requests;
- coverage wording that never converts a partial/discovered corpus into retailer completeness; and
- exact GitHub-hosted catalog + iOS validation on candidate and integrated `main`.
