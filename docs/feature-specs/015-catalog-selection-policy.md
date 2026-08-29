# 015 — Germany catalog selection policy

**Status:** Accepted
**Last reviewed:** 2026-08-29

## Purpose

The production Germany catalog is a curated offline evidence projection, not a mirror of every record present in an upstream product database. It prioritizes packaged and processed foods where ingredients, additives, processing, certification, formulation, or review evidence can materially change what the app should explain.

Obvious basic whole foods may be omitted from the detailed product projection only through explicit, versioned rules. Omission is a storage/review prioritization decision and never a halal assessment.

The canonical v1 selection artifacts are:

- `Data/selection/catalog-selection-policy-v1.json` — versioned decision rules;
- `Data/selection/catalog-selection-policy-v1.schema.json` — policy contract;
- `Data/selection/selection-candidates-v1.schema.json` — normalized source-adapter handoff contract;
- `Data/selection/sample-selection-candidates-v1.json` — synthetic acceptance fixture; and
- `Tools/catalog_selection.py`, `Tools/catalog_selection_contract.py`, and `Tools/catalog_selection_engine.py` — standard-library CLI, contract validator, and pure decision/reporting engine.

## Source-adapter boundary

The selection policy does not depend directly on one Open Food Facts export/API revision or another source's raw JSON shape.

- **HF-SELECTION-001:** A source adapter converts upstream records into the normalized v1 candidate contract before selection.
- **HF-SELECTION-002:** The adapter preserves source record identity, original barcode representation, market, product type, source category tags, product/brand identity, ingredient text/count when available, retailer keys, and optional remote-image references needed for audit/reporting.
- **HF-SELECTION-003:** Source-specific taxonomy resolution produces reviewed source-independent `categorySignals`. The adapter must record the source snapshot/schema/taxonomy version used to derive those signals. A broad taxonomy ancestor must not be mapped to a basic-food signal when processed descendants could inherit it.
- **HF-SELECTION-004:** Source-specific formulation parsing may emit `formulationSignals`; unrecognized/missing signals must not make exclusion easier.

Issue #8 owns the Open Food Facts adapter/mapping and must consume this policy rather than duplicating its decision rules.

## Eligibility and invalid-source exclusions

Before basic-food rules are evaluated, a candidate must be usable by the Germany barcode product contract.

The v1 policy distinguishes these invalid/source exclusions from basic-food exclusions:

- `non-food` — source product type is outside the accepted food catalog;
- `wrong-market` — record is outside the policy target market;
- `source-assigned-no-barcode` — source explicitly identifies a record as having no real retail barcode;
- `unsupported-barcode-kind` — barcode provenance/kind is unsupported; and
- `invalid-or-unsupported-barcode` — barcode is not a valid GTIN-8/UPC-A/GTIN-13/GTIN-14 with a valid check digit.

- **HF-SELECTION-005:** Accepted retail barcodes normalize to 14 digits with leading zeros preserved.
- **HF-SELECTION-006:** Invalid/source exclusions and basic-food exclusions remain separate in outputs and metrics.

## Inclusion precedence

For an otherwise eligible Germany record, inclusion overrides run before basic-food rules. This prevents broad categories from hiding meaningful formulation evidence.

A candidate enters the detailed catalog when any accepted signal establishes value, including:

1. existing retailer, certification, review, or correction evidence;
2. a formulation signal such as multi-ingredient, additive, compound, flavoured, fortified, enzyme, culture, rennet, gelatine, or alcohol-related;
3. an included category signal such as bakery, prepared food, processed dairy, meat/substitute, confectionery, snack, dessert, sauce/condiment/seasoning, processed spread, formulated beverage, or methodology-high-interest;
4. a known ingredient count greater than one; or
5. non-empty ingredient text whose ingredient count is not reliably known.

- **HF-SELECTION-007:** Inclusion evidence wins over a simultaneous basic-food category signal. A processed apple product, flavoured milk, or flavoured water cannot be excluded merely because an upstream taxonomy also places it under fruit, milk, or water.
- **HF-SELECTION-008:** Existing evidence that makes a product useful to the evidence/review workflow is an inclusion override even if the food itself would otherwise look basic.

## Approved basic-food exclusions

Version 1 has only three basic exclusion reason codes:

- `basic-fresh-produce` for reviewed fresh-fruit, fresh-vegetable, and basic-herb signals;
- `basic-plain-milk` for explicit plain cow-milk signals; and
- `basic-plain-water` for explicit plain-water signals.

Each rule permits at most one ingredient and is considered only after all inclusion overrides.

- **HF-SELECTION-009:** Exclusion requires an explicit approved basic category signal. Missing ingredient text by itself never excludes a record.
- **HF-SELECTION-010:** Unknown/unmapped category with missing ingredients remains in the detailed projection under `conservative-unknown` so the app/review flow can represent uncertainty instead of guessing it is basic.
- **HF-SELECTION-011:** Adding or broadening a basic-food rule requires a policy-version change, reviewed fixtures, and an impact report.

## Exclusion is not an assessment

- **HF-SELECTION-012:** The selection evaluator never emits `halal-certified`, `halal-reviewed`, `not-halal`, `questionable`, or any other consumer assessment.
- **HF-SELECTION-013:** A compact basic exclusion index may contain only normalized GTIN, market, policy version, and stable exclusion reason. It must not contain ingredients, remote images, review records, certification records, or a positive halal verdict.

Issue #30 decides whether that compact index's measured SQLite cost is worth shipping. Until then it is compiler/benchmark output, not an accepted physical database migration.

## Remote image boundary

- **HF-SELECTION-014:** Selection/acquisition tooling must not download, embed, base64-encode, or copy product image bytes into the policy input, output, SQLite catalog, or app bundle.
- **HF-SELECTION-015:** An admitted source may supply optional absolute HTTPS image references with purpose, source key, image ID/revision, and bounded dimensions. Selection preserves only that metadata.
- **HF-SELECTION-016:** Image presence or network availability never changes selection, barcode lookup, assessment, or reason semantics.

## Determinism and reporting

`catalogSelectionPolicy.policyVersion` uses semantic versioning independently of the catalog data version.

For a fixed normalized source snapshot and policy version:

- decisions are independent of input array/signal order;
- detailed output is sorted deterministically;
- basic and invalid exclusions are sorted deterministically;
- the exclusion audit sample is chosen deterministically from policy version + record identity + reason; and
- logical payload byte counts use canonical JSON so policy comparisons are reproducible.

- **HF-SELECTION-017:** Every evaluation reports records examined, target-market records, eligible Germany candidates, detailed inclusions, basic exclusions by reason, invalid exclusions by reason, included records with/missing ingredients, logical detailed/exclusion-index bytes, top category/brand/retailer values, and a deterministic excluded-basic sample.
- **HF-SELECTION-018:** A policy change must support a before/after comparison that identifies changed decisions/reasons and inclusion/basic/invalid count deltas before publication.

Measured SQLite size/query/build costs remain owned by issue #30; this policy reports logical payload size only.

## Security and execution

- **HF-SELECTION-019:** Policy validation/evaluation is standard-library-only, local-file-only, and performs no network access.
- **HF-SELECTION-020:** Unexpected candidate/image fields, non-HTTPS image references, unsupported required schema versions, malformed policy rules, duplicate source record IDs, and malformed normalized fields fail closed.
- **HF-SELECTION-021:** Source acquisition is separate from selection. Evaluating a policy must never cause an implicit source refresh or image download.

## Acceptance fixtures

The committed synthetic fixture must cover at least:

- fresh apple/cucumber/tomato → basic exclusion;
- plain single-ingredient cow milk → basic exclusion;
- flavoured milk → detailed inclusion;
- bakery item with missing ingredients → detailed inclusion;
- bread with enzyme/additive signals → detailed inclusion;
- unknown packaged product with missing ingredients → conservative detailed inclusion;
- plain water → basic exclusion;
- flavoured water → detailed inclusion;
- processed apple sauce inheriting a fruit signal → detailed inclusion;
- a basic herb with retailer evidence → detailed inclusion;
- optional HTTPS image metadata → retained without bytes;
- non-food, invalid barcode, source-assigned no-barcode, and wrong-market records → distinct invalid exclusions.
