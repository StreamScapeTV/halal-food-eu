# 010 — Catalog pipeline and release

**Status:** Accepted  
**Last reviewed:** 2026-08-29

## Pipeline stages

1. **Acquire** from an approved source and capture source/license metadata.
2. **Stage** immutable raw observations outside the app bundle.
3. **Normalize** GTIN, language, whitespace, dates, taxonomy/category signals, and ingredient tokens without destroying source text.
4. **Select** detailed Germany catalog candidates through the accepted versioned selection policy, preserving explicit basic/invalid exclusions and metrics.
5. **Detect change** using source record and ingredient hash.
6. **Assess** through a versioned rule/review process.
7. **Build** a new SQLite file from deterministic ordered inputs.
8. **Validate** logical constraints, selection metrics, query plans, integrity, license compatibility, and digest.
9. **Review** sampled records, deterministic basic exclusions, and all changed high-risk assessments.
10. **Release** the database and manifest inside a tested app build.

## Accepted requirements

- **HF-PIPELINE-001:** Catalog construction occurs outside the iOS runtime through version-controlled tooling.
- **HF-PIPELINE-002:** Builder inputs are machine-readable and ordered deterministically.
- **HF-PIPELINE-003:** A build must not download remote data implicitly. Acquisition is a separate auditable step.
- **HF-PIPELINE-004:** The builder validates GTIN check digits and rejects duplicates after normalization.
- **HF-PIPELINE-005:** Every real record must have a source, ingredient observation date/retrieval date, license, and attribution.
- **HF-PIPELINE-006:** Every non-`unknown` assessment must identify methodology version, review date, summary, and reasons.
- **HF-PIPELINE-007:** Changed ingredients invalidate the previous current assessment until the new observation is reviewed.
- **HF-PIPELINE-008:** The manifest digest is computed after the database is finalized.
- **HF-PIPELINE-009:** CI compares rebuilt and committed catalogs by logical content rather than requiring byte-identical SQLite output across library versions.
- **HF-PIPELINE-010:** Production release notes summarize record count, additions, formulation changes, status changes, stale records, source/license changes, schema version, and methodology version.
- **HF-PIPELINE-011:** Production catalog proposals record the selection-policy version and report records examined, included detailed products, basic exclusions, invalid/source exclusions, missing-ingredient inclusions, deterministic exclusion samples, and policy decision changes.
- **HF-PIPELINE-012:** A selection-policy change must produce a deterministic before/after impact report before publication; a decrease in detailed coverage is review evidence, not an automatic optimization.
- **HF-PIPELINE-013:** Selection/build stages may preserve bounded remote image-reference metadata but must not implicitly download or bundle product image bytes.
- **HF-PIPELINE-014:** Every releasable catalog manifest records the exact reviewed source-policy schema/version and SHA-256, and CI verifies that binding before artifact handoff or release evidence is accepted.

## Versioning

`catalogVersion` follows semantic versioning:

- MAJOR — incompatible interpretation or broad source/methodology reset.
- MINOR — added/updated product records or re-reviews under compatible schema/meaning.
- PATCH — metadata/provenance corrections that do not alter assessment meaning.

Prerelease labels such as `demo.1` identify non-production synthetic catalogs.

## Rollback

Because the initial catalog ships in the app bundle, rollback occurs through a new app release containing the last known-good catalog. A future separately downloaded catalog requires signature verification, atomic replacement, compatibility checks, and retained previous version before it can become Accepted.
