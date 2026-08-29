# 014 — Immutable evidence exchange and runtime projection

**Status:** Accepted  
**Last reviewed:** 2026-08-29

## Purpose

Acquisition sources, package submissions, retailer observations, certification records, reviews, halal assessments, and the iOS runtime do not share one mutable product row. They exchange immutable evidence through a versioned, source-independent contract.

The canonical machine-readable v1 contract is:

- `Data/evidence/evidence-envelope-v1.schema.json`;
- validated by `Tools/evidence_model.py`;
- demonstrated by `Data/evidence/sample-evidence-v1.json`; and
- represented by immutable Swift values in `HalalFoodEU/Domain/Models/EvidenceModels.swift`.

This specification defines evidence semantics. Source precedence/freshness selection is owned by specification 005 and issue #10; halal interpretation is owned by specification 004/013 and issue #11; final SQLite compilation is owned by specification 003/010 and issue #12.

## Envelope invariants

- **HF-EVIDENCE-001:** Every exchange artifact carries an explicit integer `schemaVersion`. Version 1 consumers reject unsupported future required versions instead of silently weakening semantics.
- **HF-EVIDENCE-002:** Evidence records are immutable. A new source revision, formulation, certificate state, review decision, or other materially different observation creates a new record rather than rewriting history.
- **HF-EVIDENCE-003:** Stable evidence IDs use the namespaced form `hfeu:<kind>:sha256:<digest>` derived from canonical JSON. Rerunning the same normalized input produces the same ID.
- **HF-EVIDENCE-004:** Canonical GTIN storage is exactly 14 digits with leading zeros preserved and a valid check digit. The exact market is part of product/formulation identity.
- **HF-EVIDENCE-005:** Source keys, source record IDs/revisions, observation/retrieval timestamps, and evidence references remain traceable across staging, assessment, release, and runtime projection.
- **HF-EVIDENCE-006:** Unknown required enum values, dangling references, cross-market links, invalid hashes, and unsupported schemas fail closed.

## Source references

A source reference records a stable source key, operator, source class, canonical reference, access method, market scope, retrieval timestamp, and optional source snapshot/revision/modified timestamp.

The exchange contract records technical provenance and access semantics only. Credentials, authenticated URLs, private contracts, and secret values never belong in an evidence envelope.

## Product identity observations

Product identity evidence records:

- stable evidence ID;
- canonical GTIN and original barcode representation;
- market;
- source record/revision;
- product name, brand/brand owner, quantity, category, and packaging observations where available;
- observation/retrieval/source-modified timestamps; and
- confidence/conflict state.

Product name is not an identifier. Different markets or variants may share names/GTINs without inheriting formulations or assessments automatically.

## Ingredient observations

- **HF-EVIDENCE-007:** Ingredient text is stored verbatim in its original language. Normalized tokens or translations are derived data and never replace source text.
- **HF-EVIDENCE-008:** `contentHash` is SHA-256 over canonical JSON containing exact ingredient, allergen, and traces text. A changed formulation-relevant text produces a different hash.
- **HF-EVIDENCE-009:** Ingredient capture method and verification state are explicit. OCR/translation metadata records tool/version/confidence when present.
- **HF-EVIDENCE-010:** Supersession may link a new ingredient observation to the older observation it replaces, but only for the same GTIN and market and without cycles.
- **HF-EVIDENCE-011:** Missing ingredients do not produce an empty synthetic ingredient observation. Product identity may exist without ingredient evidence, leading to `unknown`/review/submission handling downstream.

## Retailer evidence

Retail evidence is separate from formulation evidence and has one of three meanings:

1. `retailer-feed-listing` — an official feed/snapshot listing under the source's stated scope;
2. `retailer-observation` — dated proof/price/location observation; or
3. `community-store-report` — a community/open-database store report.

A retailer record cannot refresh ingredient/certification evidence and cannot imply nationwide/current stock unless its source semantics explicitly support that claim.

## Remote product images

- **HF-EVIDENCE-012:** Product image binaries, thumbnails, base64 payloads, and local image blobs are not part of the evidence envelope or normal runtime SQLite projection.
- **HF-EVIDENCE-013:** Optional product image metadata may contain only an HTTPS remote URL/reference, source/image identifier/revision, purpose, timestamps, and bounded dimensions.
- **HF-EVIDENCE-014:** Image availability is never required for barcode lookup, assessment, or evidence explanation.

Remote presentation images are distinct from user/package evidence used during review.

## User/package evidence references

Package evidence records the GTIN/market, evidence purpose, SHA-256, observation date, consent/privacy/redaction/verification state, and a bounded internal review-artifact reference.

Sender identity, email address, receipts, faces, unrelated personal data, and image bytes do not enter the runtime projection.

## Certification evidence

Certification evidence records:

- certifier and scheme;
- certificate reference;
- exact GTIN/market matching basis;
- product/facility/batch/scope where available;
- source record/revision;
- issue/effective/expiry/revocation/suspension/last-checked dates; and
- immutable evidence hash where applicable.

Structural presence of a certificate is not itself acceptance of that certifier/scheme; methodology and review governance still apply.

## Review records

Evidence review state is independent from the consumer-facing halal status.

Accepted review states remain:

- `unreviewed`;
- `in-review`;
- `approved`;
- `rejected`;
- `superseded`.

A review stores target evidence/assessment ID, durable reviewer identifier, timestamp, decision code/reason, and optional methodology/tool context. Reviewer identity must not expose unnecessary personal data.

## Halal assessments and validity

- **HF-EVIDENCE-015:** An assessment is immutable and links the exact GTIN/market, methodology version, formulation/certification/evidence IDs, structured reasons, and assessment date.
- **HF-EVIDENCE-016:** `halal-certified` structurally requires linked certification evidence; accepted certification scope/meaning remains governed by specification 004/013.
- **HF-EVIDENCE-017:** Assessment invalidation/supersession/restoration is represented by separate validity events so historical assessment facts remain auditable.
- **HF-EVIDENCE-018:** A current assessment must be approved, must bind to the selected current formulation, and must not be invalidated, rejected, or tied to a superseded formulation. Missing formulation may only expose an `unknown` assessment.

## Current selection and runtime projection

The evidence envelope contains explicit `currentSelections`. They represent the result of upstream source-selection/conflict/freshness/review policy; the v1 evidence tool validates them but does not invent source precedence.

For each GTIN/market, a current selection references:

- identity observation;
- current ingredient observation or explicit missing state;
- current assessment when valid;
- relevant certifications;
- type-separated retailer evidence;
- optional remote image references; and
- explicit conflict flags.

`Tools/evidence_model.py project` emits a deterministic minimal runtime projection ordered by GTIN/market.

- **HF-EVIDENCE-019:** The runtime projection keeps fields required for offline barcode/result UX and compact source references, not raw source snapshots, obsolete history, review records, package-submission artifacts, or image binaries.
- **HF-EVIDENCE-020:** Package review evidence and source history may live in workflow/operational storage without increasing the iPhone SQLite footprint. Final SQLite compilation must preserve the evidence IDs needed to trace user-visible facts.

## Release evidence

Catalog release evidence records:

- catalog/schema/methodology/selection-policy versions;
- source snapshot IDs and digests;
- builder/workflow commit identity;
- runtime projection digest; and
- product/observation/assessment/review summary counts.

This is release lineage, not a replacement for source-specific raw audit storage.

## Compatibility

Version 1 is the only accepted exchange schema initially.

- Producers must emit v1 until an accepted migration updates producers and consumers together.
- Consumers reject newer unsupported required schema versions.
- The existing demo SQLite schema remains compatible until issue #12 deliberately changes the physical database.
- Schema migrations happen in build tooling; the iOS app never performs hidden write migrations against the bundled catalog.

## Security and privacy

The validator and projector:

- use only local files and standard-library code;
- perform no network access;
- reject unexpected fields in evidence records;
- reject non-HTTPS remote image references;
- preserve source text rather than executing/rendering it; and
- exclude user/package review artifacts from the runtime projection.

All external source payloads remain untrusted input and receive additional workflow hardening under issue #23.
