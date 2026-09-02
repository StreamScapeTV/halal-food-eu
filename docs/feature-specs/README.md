# Halal Food EU feature specifications

## Authority

This directory is the canonical product and engineering source of truth. Accepted requirements here override issue descriptions, comments, prototypes, and implementation assumptions. A behavior-changing pull request must update the affected specification in the same change.

Specifications use requirement identifiers so code, tests, catalog validators, issues, and release notes can cite exact obligations.

## Status vocabulary

- **Accepted** — required now; implementation must conform.
- **Proposed** — designed but not yet binding; promotion to Accepted requires review.
- **Future** — intentionally outside the current release boundary.
- **Rejected** — considered and deliberately excluded, with rationale retained.

Unless a section says otherwise, requirements in documents 001 through 011 and 013 through 024 are **Accepted**. Document 012 is **Future**.

## Product invariant

Halal Food EU helps a person scan a packaged-food barcode and understand the best available evidence about the product’s halal status. It must be fast, private, offline-capable, transparent about uncertainty, and explicit about when ingredient or certification evidence was observed.

It must not turn missing information into a positive ruling. A product can be `halal-certified`, `halal-reviewed`, `not-halal`, `questionable`, or `unknown`; the UI must explain why.

## Current specification map

| Document | Authority |
| --- | --- |
| [001 Product vision and scope](001-product-vision-and-scope.md) | users, platform, goals, exclusions |
| [002 Scanning and lookup](002-scanning-and-lookup.md) | barcode capture, normalization, manual entry, lookup behavior |
| [003 Catalog and SQLite](003-catalog-and-sqlite.md) | bundled database, schema, indexing, runtime access |
| [004 Halal assessment](004-halal-assessment-and-explanations.md) | statuses, evidence, reasons, conflicts, uncertainty |
| [005 Data sourcing](005-data-sourcing-provenance-and-freshness.md) | lawful ingestion, provenance, observations, freshness |
| [006 Search, history, and feedback](006-search-history-and-feedback.md) | local user features and not-found reporting |
| [007 Native design](007-native-design-accessibility-and-localization.md) | SwiftUI, Liquid Glass adoption, accessibility, languages |
| [008 Privacy and security](008-privacy-security-and-safety.md) | local processing, integrity, claims, threat boundaries |
| [009 Performance and reliability](009-performance-and-reliability.md) | latency, concurrency, cancellation, failure handling |
| [010 Catalog releases](010-catalog-pipeline-and-release.md) | reproducible builds, validation, version compatibility |
| [011 Testing](011-testing-and-acceptance.md) | test pyramid and release gates |
| [012 Future roadmap](012-future-roadmap.md) | non-binding capabilities and prerequisites |
| [013 Methodology governance](013-methodology-and-review-governance.md) | qualified review, automation boundaries, auditability |
| [014 Evidence exchange](014-evidence-exchange-and-runtime-projection.md) | immutable cross-stage evidence contract, deterministic IDs, current selection, runtime projection |
| [015 Catalog selection](015-catalog-selection-policy.md) | Germany packaged/processed focus, explicit basic exclusions, normalized source-adapter contract, selection metrics |
| [016 Catalog workflow orchestration](016-catalog-workflow-orchestration.md) | trusted stage boundaries, handoffs, proposals, health and release orchestration |
| [017 Project configuration](017-project-configuration.md) | reviewed public configuration and fail-closed optional credential contracts |
| [018 Product evidence submission](018-product-evidence-submission.md) | backend-free iOS package-evidence preparation, consent and Mail/share/copy delivery |
| [019 Product evidence intake](019-product-evidence-intake.md) | owner admission, hostile-image validation, local OCR, human verification and non-image Git proposal |
| [020 Offline product evidence presentation](020-offline-product-evidence-presentation.md) | offline SQLite detail projection, evidence precedence, retailer wording, assessment explanations and accessibility |
| [021 EU additive reference](021-eu-additive-reference.md) | reviewed E-number identity/reference data, conservative EN/DE matching, change impact and offline explanation boundaries |
| [022 Manufacturer provenance](022-manufacturer-provenance.md) | bounded producer-origin field provenance, mediated-source semantics, review queues and no-inference boundaries |
| [023 Certifier registry](023-certifier-registry-and-certificate-validity.md) | versioned certifier/scheme admission, exact certificate eligibility, invalidation and offline certificate presentation |
| [024 Catalog health](024-catalog-health-and-coverage-reporting.md) | deterministic coverage, freshness, review, retailer claim, source and runtime health reporting |

## Conflict resolution

1. Privacy, licensing, truthful evidence, and safety requirements take precedence over feature convenience.
2. A newer accepted requirement with an explicit supersession note wins over an older one.
3. When requirements remain ambiguous, choose the behavior that exposes uncertainty and preserves evidence rather than silently inferring certainty.
4. Record a clarifying specification change before merging implementation that materially changes user-visible meaning.
