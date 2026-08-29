# 011 — Testing and acceptance

**Status:** Accepted  
**Last reviewed:** 2026-08-29

## Test layers

### Domain unit tests

- GTIN validation, check digits, normalization, and GS1 Digital Link extraction.
- Assessment status decoding and display-independent meaning.
- Lookup use-case found/not-found/error behavior.
- Freshness boundary calculations.
- Immutable evidence value decoding/round-trip under Swift 6 strict concurrency.

### Evidence-contract tests

- Validate the committed schema-v1 synthetic evidence envelope.
- Deterministic namespaced IDs and stable projection under input-array reordering.
- GTIN leading-zero/check-digit and cross-market separation.
- Ingredient content-hash and supersession/cycle behavior.
- Retailer evidence type separation.
- Certification structural linkage and review state.
- Assessment invalidation without historical mutation.
- Unknown schema/enum rejection.
- HTTPS-only remote image references with no image bytes.
- User/package review evidence excluded from the minimal runtime projection.

### Catalog-selection tests

- Validate policy and normalized candidate schemas against the semantic validator field contract.
- Explicit fresh-produce/plain-milk/plain-water exclusions, including conservative handling when milk/water ingredient count is unknown.
- Processed/formulation/certification-review-correction/category inclusion overrides before basic rules; retailer presence alone does not override a basic-food rule.
- Missing ingredients and unknown category remain conservative detailed inclusions.
- Invalid/non-food/source-assigned-no-barcode/wrong-market reasons remain distinct.
- Remote images are HTTPS metadata only; binary/base64/data-URL fields fail closed.
- Selection output, compact exclusion index, metrics, sample, and policy comparisons are deterministic under input reordering.
- Basic exclusion output never contains halal status or full product evidence.

### Security/adversarial tests

- Bounded strict-UTF-8 JSON rejects malformed, oversized, deeply nested, control-character, and excessive-collection inputs.
- URL admission rejects non-HTTPS, embedded credentials, non-allowlisted hosts/paths, localhost, private/link-local/reserved address literals, and metadata endpoints.
- Archive ingestion rejects traversal, absolute/backslash paths, symlinks/devices, excess entries, excess expansion, and suspicious compression ratios.
- Terminal/log and CSV/spreadsheet injection is neutralized; secret canaries must not appear in generated output.
- Product image bytes remain outside the admitted catalog contract.
- Workflow dependency pins match the reviewed tooling manifest; pull-request validation cannot gain write/secret authority.
- Bundled iOS catalog lookup rejects manifest/database digest mismatch, unsupported manifest/source-policy schemas, integrity failures, missing required tables, and incompatible SQLite metadata.

### Data integration tests

- Open the actual bundled SQLite fixture read-only.
- Find known synthetic GTINs and load ordered reasons.
- Return nil for a valid absent GTIN.
- Reject unsupported status/schema fixtures.
- Validate source/date/methodology mapping.
- Confirm exact lookup uses an index in catalog validation.

### Feature/UI tests

- Manual entry works without camera.
- Invalid barcode never performs lookup.
- Found/not-found/failure states are distinct.
- Status, reason, date, and source accessibility labels/read order are correct.
- Camera unavailability retains a complete manual path.

### Catalog tests

- SHA-256 and manifest metadata.
- Source-policy schema/version/SHA-256 binding.
- SQLite integrity and foreign keys.
- Unique normalized GTINs and valid check digits.
- No dangling current observation or assessment references.
- Allowed statuses/reason severities.
- Required provenance/license fields and parseable UTC dates.
- Logical equivalence after rebuild.

## CI acceptance gates

- **HF-TEST-001:** Every push and pull request runs catalog validation on a GitHub-hosted Linux runner.
- **HF-TEST-002:** Every push and pull request generates the Xcode project and builds/tests on a GitHub-hosted macOS 26 runner.
- **HF-TEST-003:** CI does not require Central CI, self-hosted runners, secrets, signing, or a backend.
- **HF-TEST-004:** The iOS job records Xcode/Swift versions and chooses an available iPhone simulator dynamically.
- **HF-TEST-005:** Warnings introduced by project code are treated as defects; release builds may progressively enable warnings-as-errors after baseline cleanup.
- **HF-TEST-006:** A pull request is opened only after the issue branch is complete and its push CI is green.
- **HF-TEST-007:** A real-data release additionally requires source/provenance and sampled assessment review; green compilation alone is insufficient.
- **HF-TEST-008:** Every push/PR that affects the evidence contract runs its stdlib validator, deterministic projection tests, schema JSON parse, and Swift fixture decoding before merge.
- **HF-TEST-009:** Every push/PR that affects catalog selection runs the versioned policy/candidate validators, synthetic decision fixtures, deterministic reporting/comparison tests, and image-boundary tests on the GitHub-hosted catalog lane.
- **HF-TEST-010:** Every push/PR that affects catalog ingestion, workflow trust, dependencies, or runtime integrity runs the adversarial security suite and emits a deterministic reviewed-tooling SBOM.

## Definition of done

A feature is done only when its accepted requirement IDs are implemented, automated tests cover material behavior, documentation and data notices are current, accessibility/privacy/concurrency have been reviewed, and the merged `main` result is green.
