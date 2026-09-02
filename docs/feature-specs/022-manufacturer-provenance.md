# 022 — Manufacturer and producer-origin formulation provenance

**Status:** Accepted  
**Last reviewed:** 2026-09-02

## Purpose

Manufacturer-origin evidence can materially improve confidence in product identity and formulation data, but origin provenance is not the same thing as freshness, package verification, certification, retailer availability, or a halal ruling.

The first production cohort uses producer-origin metadata already published through the admitted Open Food Facts database. This does not create a separate manufacturer source identity: Open Food Facts remains the licensed source and redistribution boundary. Future direct manufacturer APIs/feeds require their own source admission and rights review.

## Evidence boundary

- **HF-MFG-001:** Manufacturer/producer provenance may be asserted only for a specific field when upstream provenance binds that exact field to a producer contribution. A product-level producer account, brand name, generic `data_sources` tag, or manufacturer relationship alone is insufficient.
- **HF-MFG-002:** When manufacturer data is mediated through an admitted open database, the evidence keeps that database as `sourceKey`, source record/revision, license and attribution. The pipeline must not invent a direct manufacturer source key or imply a direct contract with the manufacturer.
- **HF-MFG-003:** The initial Open Food Facts cohort may confirm an exact producer-provided field when a bounded `owner_fields` value equals the exact current source value. `sources[].manufacturer`/`sources[].fields` may provide corroborating import context, but without exact-value binding it is only a provenance candidate requiring review.
- **HF-MFG-004:** Raw `owner_fields`, arbitrary producer metadata, PIM payloads and unbounded source arrays must not be retained in the acquisition snapshot. Acquisition keeps only an allowlisted, size-bounded provenance projection needed for exact matching and audit.
- **HF-MFG-005:** Producer provenance is immutable and keyed to the canonical evidence ID plus source record/revision and exact field/value hash. A changed formulation creates a new ingredient observation and therefore a new provenance record rather than rewriting the old record.
- **HF-MFG-006:** Producer provenance never upgrades `verificationState`, invents `observedAt`, refreshes stale evidence, creates a positive/final-negative halal status, proves certification, or proves retailer availability. Source-modified/import timestamps are supplementary provenance dates only.
- **HF-MFG-007:** Ambiguous/conflicting producer provenance remains review work. Multiple competing manufacturer-source records for one exact field, producer metadata without exact-value binding, or field/value disagreement must not be represented as confirmed producer-provided formulation evidence.

## Workflow-side audit sidecar

The v1 runtime evidence envelope remains unchanged. Producer provenance is emitted as a deterministic workflow-side sidecar keyed to canonical identity/ingredient evidence IDs.

- **HF-MFG-008:** The sidecar contains no duplicate ingredient text. It records the exact field name, SHA-256 of the source value, producer/import provenance, source record/revision and limitations; the canonical ingredient observation remains the source of verbatim ingredient text.
- **HF-MFG-009:** Sidecars may coexist in normalized workflow artifacts, but the existing `normalized-evidence` handoff remains digest-bound to `payload/evidence.json`. Runtime SQLite/iOS data does not grow merely to preserve producer audit metadata.
- **HF-MFG-010:** Direct manufacturer web pages must not be bulk scraped as a shortcut. A future manufacturer API/feed/page source is admitted only when collection and redistribution rights are reviewed and documented under specification 005.

## Manufacturer target queue

The workflow produces a versioned, deterministic queue for bounded review/prioritization.

- **HF-MFG-011:** Queue reasons distinguish confirmed producer formulation evidence, producer-provenance candidates/ambiguities, missing ingredients, formulation changes, ingredient-field deletions and other explicit evidence conflicts.
- **HF-MFG-012:** Metrics report selected products, ingredient observations, confirmed producer-origin formulations, ambiguous candidates, producer identifiers, source-modified-date coverage and relevant review reasons. A count is not a complete manufacturer denominator.
- **HF-MFG-013:** Reports must explicitly state that producer-origin coverage is partial and mediated through the upstream open database. They may not claim complete manufacturer coverage or improved formulation freshness merely because producer provenance exists.

## Open Food Facts producer cohort

Open Food Facts schema/API metadata documents producer imports through fields including `owner`, `owner_fields`, `data_sources*`, and `sources` records with manufacturer/import metadata. The adapter uses only reviewed stable fields required for this contract.

- **HF-MFG-014:** The acquisition projector may retain a bounded project-owned `_hfeu_producer_provenance` object derived from these upstream fields; it must never persist arbitrary raw producer objects.
- **HF-MFG-015:** The first cohort is considered proven only when fixtures/tests demonstrate: exact owner-field match, manufacturer-source candidate without exact-value promotion, ambiguity handling, changed-formulation invalidation, no halal/retailer inference, deterministic reports, and existing OFF source/license semantics unchanged.

## Release and future direct sources

A later direct brand/manufacturer feed can supersede this mediated cohort in evidence priority only after its own rights, GTIN/market/variant matching, formulation dates/revisions, attribution and source contract pass review. Existing Open Food Facts producer-origin observations remain historical evidence and must not be rewritten.
