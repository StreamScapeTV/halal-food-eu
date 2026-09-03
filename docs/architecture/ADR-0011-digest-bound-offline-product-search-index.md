# ADR-0011 — Digest-bound offline product search index

**Status:** Accepted  
**Date:** 2026-09-03

## Context

The accepted product-search contract requires fully offline discovery by product name, brand, and barcode while preserving exact barcode lookup as the authoritative product-identity path. Search must use indexed/FTS paths, return bounded pages, keep the bundled catalog read-only at runtime, and remain independent of retailer-coverage completion.

The production catalog already has a stable schema-v2 semantic projection for products, evidence, assessments, provenance, and compact exclusions. Product search needs additional read-optimized structures, but it does not add or reinterpret evidence fields and must not force raw acquisition columns into the app database.

## Decision

Keep the production semantic SQLite schema at **v2** and compile a separately versioned, deterministic search index into the same immutable database before release binding.

The search index is declared in `catalog-manifest.json` as `searchIndex.schemaVersion = 1` and contains:

1. an FTS5 virtual table, `product_search`, containing only canonical GTIN, product name, and brand;
2. a `product_barcode_aliases` WITHOUT ROWID table keyed by `(alias, gtin)` for canonical GTIN-14 and valid leading-zero-derived EAN-13/UPC-12/EAN-8 display forms;
3. Unicode61 tokenization with diacritic removal and reviewed prefix indexes for name/brand discovery; and
4. a maximum page-size contract of 50 rows.

These structures are **derived runtime indexes**, not new product/evidence semantics. Therefore:

- SQLite `PRAGMA user_version` remains 2;
- the production logical-catalog identity continues to hash the semantic tables and semantic metadata, not the reproducible derived index representation;
- the physical SQLite SHA-256 and byte count are recomputed after search-index materialization, so the shipped index bytes are still cryptographically bound by the production manifest;
- the existing catalog-size budget is rechecked after index materialization; and
- runtime performs no migration, FTS build, or database write.

A future change that adds search-only stored semantics that cannot be deterministically regenerated from schema-v2 products, or changes product/evidence meaning, requires a catalog-schema review instead of extending this auxiliary-index contract silently.

## Query design

### Name and brand

Name/brand search uses FTS5 `MATCH` with bound parameters. User input is normalized at the domain boundary, tokenized into bounded prefix terms, and never interpolated into SQL. Results carry compact identity fields only. FTS rank is followed by stable product-name/GTIN ordering.

### Barcode

Numeric input uses the primary-keyed alias table. Prefix search is implemented as the bounded range:

- `alias >= prefix`
- `alias < prefix || ':'`

because all admitted aliases contain ASCII digits only and `:` sorts immediately after `9`. Exact aliases rank before prefix-only matches. The selected row still resolves through the existing exact `ProductCatalog` lookup before any assessment/evidence is shown.

## Integrity and validation

Build validation requires:

- manifest schema v3 and production catalog schema v2;
- expected SQLite application ID and user version;
- manifest/database byte-count and SHA-256 agreement before and after indexing;
- FTS5 availability at build time;
- one FTS row and one canonical GTIN-14 alias per product;
- SQLite integrity and foreign-key checks;
- `EXPLAIN QUERY PLAN` proof that name/brand uses the FTS virtual-table index and barcode prefix uses the alias-table primary key/index;
- deterministic/idempotent installation; and
- the existing `< 250 MiB` catalog budget after search index materialization.

The iOS runtime independently checks the same manifest/index identity and opens the database read-only with `query_only` enabled.

## Consequences

- Application development and testing can use the deterministic production-shaped synthetic fixture; production retailer breadth only changes the number of searchable rows.
- Raw retailer/source acquisition fields remain outside the mobile runtime projection.
- No backend, network search, analytics, query telemetry, search history, or runtime database migration is introduced.
- Search-index storage contributes to the physical catalog-size budget and is visible in the manifest digest/byte count.
- Text results remain provisional suggestions until the user explicitly selects an exact GTIN record.

## Rejected alternatives

### Unbounded `LIKE '%query%'` scans

Rejected because they violate HF-SEARCH-004 and do not scale with the production catalog.

### Runtime FTS/index creation

Rejected because the bundled catalog is immutable/read-only and runtime migration would create launch-time failure and integrity ambiguity.

### New network/backend search service

Rejected because search is an offline core capability and a server would add availability, privacy, and operational dependencies without need.

### Mirroring acquisition CSV columns into SQLite

Rejected because acquisition/provenance staging can contain many fields that are irrelevant to mobile lookup. The runtime stays a compact evidence/product projection.
