# 030 — Git-backed bundled catalog

**Status:** Accepted  
**Date:** 2026-09-14  
**Primary issue:** #108

## Purpose

Keep the reviewed Germany runtime catalog reproducible from repository content while preserving the immutable SQLite runtime architecture. Git stores a rights-reviewed, deterministic source projection; build tooling validates and compiles that projection into `catalog.sqlite3` and `catalog-manifest.json` before Xcode packages the app. The running app never parses CSV and never needs private Drive access.

### HF-BUNDLE-001 — Versioned source-set manifest

`Data/catalog/bundled/de/source-manifest-v1.json` is the source-set authority. It binds schema version, dataset/catalog identity, market, generation timestamp, selection/quality policies, reviewed source-policy identities and SHA-256 digests, source snapshot/retrieval lineage, the shard strategy, per-shard stored byte counts and SHA-256 values, record counts, and one canonical logical digest over all rows.

### HF-BUNDLE-002 — Deterministic sharding is storage only

One logical catalog may be stored as one CSV or multiple CSV shards. Every shard contains the exact same UTF-8/LF CSV header and schema. A shard may be stored as plain `.csv` or deterministic gzip-compressed `.csv.gz`; compression changes storage bytes only, while the manifest binds both stored byte count/SHA-256 and the logical row digest.

Source-set v1 uses stable `sha256-mod` bucketing of canonical GTIN-14 values and currently stores 32 deterministic gzip-compressed shards. Shard count is a repository-maintenance detail: changing the number/order of shards must not change the logical evidence/runtime projection for identical rows.

### HF-BUNDLE-003 — Fail closed validation

The source validator rejects malformed UTF-8/LF CSV, header/schema drift, unbound or tampered shards, wrong bucket placement, unsorted rows, duplicate GTINs, invalid GTIN check digits, blank required identity fields, unsupported market/source semantics, missing/changed reviewed policy digests, and unreviewed/non-redistributable source policies. No malformed row is silently repaired during the production build.

### HF-BUNDLE-004 — Source-scoped evidence semantics

Git rows are projected into the existing evidence envelope before production compilation. Open Food Facts identity/formulation data remains Open Food Facts evidence. Open Prices rows remain dated retailer observations with location scope where available. Community store metadata remains low-confidence community evidence. The migration does not invent certifications, halal assessments, freshness dates, official Lidl claims, or current/nationwide stock claims.

### HF-BUNDLE-005 — Rights boundary

Only source material whose collection and public/bundle redistribution rights are explicitly admitted may enter the Git source set. Source-set v1 admits the reviewed Open Food Facts and Open Prices ODbL policies and preserves their attribution/share-alike identity. Public retailer/manufacturer pages are not automatically redistributable merely because they are viewable on the web.

### HF-BUNDLE-006 — Research and production remain separate

Private research masters may be larger than the public source set. Rows with no valid GTIN, invalid GTINs, missing runtime identity fields, private manufacturer/retailer evidence, or otherwise unadmitted rights remain research/quarantine material. Research Agent output does not become production merely by being present in a spreadsheet.

### HF-BUNDLE-007 — Existing quality and production compiler stay authoritative

The builder converts validated shards to the evidence envelope, runs the existing catalog-quality release gate, invokes `Tools/production_catalog.py`, installs the digest-bound offline search index, and validates the resulting SQLite and manifest. The generated production manifest additionally binds the exact Git source-set manifest SHA-256/logical digest/counts.

### HF-BUNDLE-008 — App target gets the real generated catalog

Ordinary app build/test first builds the Git-backed Germany catalog into `HalalFoodEU/Resources/catalog.sqlite3` and `catalog-manifest.json`. `AppContainer.live()` continues to open those resources read-only. No runtime migration, CSV parser, network fetch, or Drive access is introduced.

### HF-BUNDLE-009 — Exact prebuilt release artifacts remain supported

`HFEU_PREBUILT_CATALOG_DATABASE` and `HFEU_PREBUILT_CATALOG_MANIFEST` continue to override generation for exact release-artifact compatibility validation. Both files are required together and are validated before Xcode testing.

### HF-BUNDLE-010 — Synthetic data stays fixture-only

The Swift test target gets a separately generated synthetic production-shaped catalog under `HalalFoodEUTests/Resources`. This preserves deterministic product-detail/search tests without shipping synthetic products in the application catalog. In prebuilt compatibility mode, the exact supplied release catalog is copied to both app and test resources and only the artifact compatibility test is selected.

## Maintainer source update and re-shard procedure

The Git bundle is a reviewed production source, not an ingestion scratch area. A refresh starts from a rights-reviewed candidate projection outside the shipping resources and follows this sequence:

1. **Admit sources before rows.** Confirm that every source used by the candidate is represented by a reviewed policy under `Data/sources/`, that collection and public/bundle redistribution are permitted, and that attribution/share-alike obligations are compatible with the combined catalog. Private retailer/manufacturer research, unadmitted web pages, and proof/image binaries stay outside the Git bundle.
2. **Produce canonical logical rows.** Use exactly the v1 `columns` order from `source-manifest-v1.json`, UTF-8 with LF line endings, one canonical valid GTIN-14 per logical row, the target market, nonblank runtime identity, and source-scoped provenance/timestamps/URLs admitted by the bound source policy. Do not repair invalid/no-GTIN/private rows into production; quarantine or exclude them before sharding.
3. **Assign and sort shards deterministically.** Choose `bucketCount` in the reviewed `1...64` range. For each GTIN compute SHA-256 over its ASCII canonical GTIN-14, interpret the first eight digest bytes as an unsigned big-endian integer, and take modulo `bucketCount`. Every bucket uses the identical reviewed CSV header and rows are strictly ascending by GTIN. Changing bucket count/order is storage-only and must not alter the logical row set.
4. **Write only an admitted storage encoding.** Source-set v1 permits plain `.csv` or deterministic `.csv.gz`. Deterministic gzip uses an empty embedded filename, `mtime=0`, and compression level 9 (the repository tests use `gzip.GzipFile(filename="", mode="wb", mtime=0, compresslevel=9)`). Do not introduce another compression label without a new reviewed contract/schema change.
5. **Rebind the manifest from the produced bytes.** Update `datasetID`, `catalogVersion`, `generatedAt`, source snapshot/revision/reference/retrieval lineage, and any changed policy binding. For every shard record its bucket, relative path, compression, logical row count, exact stored byte count, and SHA-256 of the stored bytes. Set `recordCount` to the total logical rows. Recompute `logicalSha256` with the same canonical JSON/sort rule used by `Tools/catalog_source_shards.py`; never derive it from compressed bytes or shard filenames.
6. **Validate before building.** Run:

   ```bash
   PYTHONPATH=Tools python3 Tools/catalog_source_shards.py validate \
     --manifest Data/catalog/bundled/de/source-manifest-v1.json
   PYTHONPATH=Tools python3 -m unittest \
     Tools.tests.test_catalog_source_shards \
     Tools.tests.test_build_bundled_catalog
   make catalog
   make catalog-validate
   ```

7. **Prove a pure re-shard is logically identical.** Before replacing an existing source set solely to change shard layout, retain the previous manifest/shards outside the edited paths long enough to generate both evidence envelopes with `Tools/catalog_source_shards.py evidence`. Their canonical evidence JSON and `logicalSha256` must match exactly. The shard byte hashes may change; product/evidence identity must not.
8. **Review and CI the exact candidate.** Inspect the public diff for accidental private/unlicensed material, source-policy changes, count/digest changes, and semantic drift. The exact candidate head must pass catalog/production/iOS checks before integration. After merge, rebuild/validate the exact integrated `main` before closing the owning issue.

Generated SQLite/manifest files remain ignored build artifacts. A catalog refresh changes Git source data and its manifest, not the app’s immutable runtime architecture.

## Initial Germany migration

The September 2 private working CSV contains 7,555 observation rows. The reviewed migration excludes three manufacturer-official observation rows from the public bundle lane, seven rows with no GTIN, 35 rows whose GTIN fails canonical check-digit validation, and 70 rows with no runtime product name. Three Open Prices rows lack a usable dated observation and therefore do not project retailer-observation evidence. The first Git-backed source set contains 7,440 unique valid products, 6,563 ingredient observations, and 7,437 retailer evidence records. Products remain unreviewed/`unknown` unless separate reviewed methodology evidence supports another status.

### HF-BUNDLE-011 — Linux CI validates the real source set

Production-catalog CI validates the manifest/shards, runs the shard tests, builds the real Git-backed SQLite/search index, and checks the expected 7,440-row source identity. The existing synthetic compiler fixture remains as a separate regression path.

### HF-BUNDLE-012 — iOS CI proves packaging

iOS CI validates/builds the Git-backed catalog before Xcode generation, then `Scripts/ci-ios.sh` packages the real generated catalog in the app and the synthetic fixture in the test bundle. Hosted Swift tests therefore verify application packaging without coupling all test expectations to live catalog contents.

## Acceptance

A clean checkout can build the Germany application catalog without private Drive access or external API secrets; generated SQLite/manifest pass existing production/search validation; source licensing/provenance remains explicit; sharding/compression does not change logical catalog identity; invalid/unlicensed/research-only rows fail closed or remain excluded; and exact branch/PR/integrated CI must be green before issue closure.
