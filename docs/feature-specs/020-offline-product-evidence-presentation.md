# 020 — Offline product evidence presentation

**Status:** Accepted  
**Last reviewed:** 2026-09-01  
**Owner:** Halal Food EU product/runtime

## Purpose

Define the iOS product-detail projection and user-visible evidence semantics for the bundled production SQLite catalog. A successful barcode lookup must explain what product was found, which current evidence supports the result, which limitations apply, and why the current halal state is shown without requiring a network connection.

This specification refines 002, 003, 004, 005, 007, 008, 009, 014 and 018 for the Phase 1 product-result experience.

## Requirements

### HF-RESULT-001 — One offline detail projection

A successful lookup MUST return one immutable `Sendable` product-detail projection from the bundled read-only SQLite catalog. The repository layer MAY use a fixed bounded set of SQL statements for ordered evidence collections, but SwiftUI MUST NOT issue evidence-row queries or create UI-driven N+1 access.

The projection MUST carry, when stored by runtime schema v2:

- canonical 14-digit GTIN;
- market, product name, brand, brand owner and quantity;
- current ingredient text, source language, observed/retrieved dates, content hash, verification state and freshness;
- ingredient source operator/class/reference/license/attribution;
- current selection conflict flags;
- current assessment, methodology/review lineage and structured reasons;
- current certification scheme/reference/scope/validity/last-check/source;
- ordered retailer evidence with kind/date/scope/limitations/source;
- catalog version;
- inert remote-image references for future explicitly network-enabled presentation.

Runtime schema v2 does not store a product category in the `products` table. The app MUST NOT invent or reconstruct one for this screen merely to satisfy presentation convenience.

### HF-RESULT-002 — Exact ingredient observation remains primary

The exact stored ingredient text MUST be visible and selectable in its recorded source language. It MUST NOT be replaced by a translated, normalized or OCR-derived rewrite.

The screen MUST show absolute observation date when available, retrieval date, verification state and freshness. Date-unknown evidence MUST be described as date unknown rather than assigned an inferred date.

A future translation aid MAY be displayed separately only when it is visibly labeled derived and retains the source text as primary.

### HF-RESULT-003 — Evidence warnings outrank former positive assessments

Presentation MUST apply this safety precedence before a recorded positive status:

1. unresolved current-selection conflict flags;
2. missing current ingredient formulation;
3. `changed-unreviewed` formulation;
4. stale formulation;
5. date-unknown formulation;
6. current formulation whose verification state is not `human-verified`.

Any condition above is blocking for a recorded `halal-certified` or `halal-reviewed` status. The current display MUST become `unknown`/needs review and MUST identify the recorded positive status only as an earlier/historical result.

`refresh-recommended` is advisory: it MUST be shown before the assessment, but it does not by itself invalidate an otherwise current reviewed result.

A blocking evidence warning MUST NOT downgrade or hide a recorded `not-halal` or `questionable` result. Prohibitive or unresolved evidence remains visible.

### HF-RESULT-004 — Assessment meanings are explicit

The result screen MUST use text and symbols, not color alone.

- `halal-certified`: explain that current scope-matched certification evidence supports the result; show certifier, scheme, reference, scope, validity/expiry and last-check date when available.
- `halal-reviewed`: state that this is an ingredient/evidence review under a named methodology and is not certification.
- `not-halal`: place prohibitive reasons first and identify the evidence/ingredient/process represented by those reasons.
- `questionable`: place prohibitive/caution reasons before informational/positive reasons and explain that evidence remains unresolved.
- `unknown`: explain whether current ingredients or approved review evidence are missing or otherwise insufficient.

When a former positive assessment is blocked by HF-RESULT-003, its summary MUST be labeled as a recorded/historical summary rather than presented as the current conclusion, including in accessibility text.

### HF-RESULT-005 — Retailer wording follows evidence kind

Retailer presentation MUST remain separate from halal assessment and MUST use the stored evidence kind and date semantics.

- `retailer-feed-listing`: “Listed in the approved <retailer> feed dated <absolute date>” when a date is available.
- `retailer-observation`: “Observed at a <retailer> store on <absolute date>” when a date is available.
- `community-store-report`: “Community data reports <retailer> on <absolute date>” when a date is available.
- no retailer rows: “No retailer evidence in this catalog.”

The screen MUST show stored scope and limitations when present. It MUST NOT turn an internal `location_id` into user-visible location text unless a later accepted policy defines that identifier as approved display granularity.

The UI MUST NOT state or imply “currently in stock”, “available everywhere”, “normally sold at”, complete assortment, or nationwide availability unless a later source contract explicitly supports that stronger semantic.

### HF-RESULT-006 — Source provenance remains inspectable

Ingredient, retailer and certification sections MUST expose the stored source operator, source/evidence class, reviewed attribution or license fallback, retrieval date, and source reference.

An HTTPS source reference MAY be opened only through an explicit user action. Non-HTTPS or non-web references remain visible as text. Rendering a product MUST NOT contact source APIs.

### HF-RESULT-007 — Remote images do not break offline lookup

Remote image references may be carried in the product projection, but the Phase 1 result screen MUST NOT automatically fetch them. Product identity, ingredient evidence, assessment and retailer evidence remain complete and usable offline.

A future accepted specification may enable opt-in/on-demand image networking without allowing image availability to alter halal classification.

### HF-RESULT-008 — Native accessibility and localization

The screen MUST use native SwiftUI sections, labels, buttons and links; support Dynamic Type and VoiceOver; preserve leading/trailing layout; and provide non-color status/warning meaning through text and symbols.

All new product-result semantic strings MUST have English and German resources. Dates MUST use locale-aware formatting while retaining an absolute calendar date including the year.

VoiceOver output for an invalidated former positive assessment MUST announce the current needs-review/unknown state before identifying the earlier result.

### HF-RESULT-009 — Scanner and submission boundaries remain intact

`ScannerViewModel` remains `@MainActor` and MUST cancel an obsolete lookup task before starting a newer scan/manual request. SQLite work remains behind the injected async repository/use case.

Product-not-found and correction actions MUST continue to route through the backend-free #14 submission flow. No account or backend is introduced by this screen.

### HF-RESULT-010 — Failure states fail closed

Missing, corrupt, digest-mismatched or incompatible catalog artifacts MUST continue to surface a lookup failure rather than synthetic product data. Invalid/unsupported barcodes remain distinct from catalog failures.

The result screen MUST NOT log scanned GTINs, viewed products or evidence to analytics.

### HF-RESULT-011 — Verification

Automated verification MUST cover at least:

- production SQLite projection of canonical GTIN, market/quantity, source attribution, exact ingredients, verification state and certification lineage;
- production retailer observation kind/date/scope/limitations;
- inert HTTPS remote-image projection;
- conflict/stale/date-unknown/changed/unverified precedence over former positive results;
- preservation of `not-halal` under blocking warnings;
- evidence-first reason ordering;
- qualified retailer wording and explicit absence of stock/completeness claims;
- English/German semantic resources and absolute localized dates;
- accessibility wording that marks former positive results historical;
- existing catalog integrity/incompatibility/cancellation/submission tests.

The complete iOS target MUST compile and tests MUST pass under Swift 6 strict concurrency on the repository’s GitHub-hosted macOS/Xcode gate.

## Non-goals

- No source API or cloud service is added for product rendering.
- No product image bytes are bundled or fetched automatically.
- No new halal methodology or assessment automation is introduced.
- No retailer completeness/current-stock claim is introduced.
- No runtime schema migration is required solely for category presentation.
