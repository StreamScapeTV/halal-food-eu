# 003 — Catalog and SQLite

**Status:** Accepted  
**Last reviewed:** 2026-08-26

## Storage decision

The product catalog is a versioned SQLite database bundled as an application resource. It is immutable at runtime. Mutable user data, if introduced, belongs in a separate store and must never rewrite catalog evidence.

## Accepted requirements

- **HF-DB-001:** The application bundle must include `catalog.sqlite3` and `catalog-manifest.json`.
- **HF-DB-002:** Runtime catalog access must open SQLite read-only. Application code must not execute schema migrations, inserts, updates, deletes, or vacuum operations against the bundled catalog.
- **HF-DB-003:** The normalized 14-digit GTIN is the product primary key and must have a unique B-tree lookup path.
- **HF-DB-004:** Ingredient observations are immutable rows. A product points to its current observation while older observations can remain available for provenance/history.
- **HF-DB-005:** Assessments bind to a specific ingredient observation, not merely a product barcode.
- **HF-DB-006:** Assessment reasons are ordered structured rows with stable reason codes; they must not exist only as one opaque prose field.
- **HF-DB-007:** Every observation references a source record containing source name, type, reference, retrieval timestamp, and data license.
- **HF-DB-008:** SQL must be parameterized and statements finalized deterministically.
- **HF-DB-009:** Database access must be isolated from the main actor. Domain/UI code must not import SQLite.
- **HF-DB-010:** Barcode lookup must avoid N+1 behavior. One product/observation/assessment query and one ordered reason query are acceptable for the initial schema.
- **HF-DB-011:** Foreign keys, integrity checks, schema version, application identifier, and manifest checksum must be validated in the catalog build/release pipeline.
- **HF-DB-012:** The runtime must fail safely when the database is missing, corrupt, incompatible, or contains an unsupported status; it must not fabricate a result.
- **HF-DB-013:** The schema must use ISO-8601 UTC timestamps for machine dates and BCP-47 language tags for ingredient text.
- **HF-DB-014:** Free-text product search, when implemented, must use an indexed strategy such as FTS5 rather than wildcard scanning across the full table.

## Initial logical schema

- `catalog_metadata` — schema, catalog, methodology, license, and build metadata.
- `sources` — lawful origin and attribution for observations.
- `products` — normalized GTIN, name, brand, current observation pointer.
- `product_observations` — ingredient text snapshot, language, dates, source, content hash.
- `product_assessments` — status, summary, methodology version, review date.
- `assessment_reasons` — ordered evidence and explanation for the assessment.

The catalog builder owns physical schema creation. App code consumes documented columns through a repository and must not leak row shapes into feature UI.

## Version compatibility

- `PRAGMA user_version` is the integer schema version.
- `catalogVersion` is a semantic version for data content.
- `methodologyVersion` identifies the rule/review methodology.
- The app must reject a schema newer than it understands.
- A catalog content update with the same schema may ship in an app patch release.
- A schema change requires app code and integration tests in the same release.
