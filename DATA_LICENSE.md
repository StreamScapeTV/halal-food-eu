# Catalog data licensing and attribution

Software and catalog data are different works and do not automatically share a license.

## Current bundled production catalog

The shipping Germany catalog is generated at build time from the reviewed Git-tracked source set under `Data/catalog/bundled/de/`. The source-set manifest is `Data/catalog/bundled/de/source-manifest-v1.json`; it binds source-policy identities/digests, attribution, deterministic CSV shards, per-shard hashes/counts, and one logical data digest.

Source-set v1 contains only data admitted through the reviewed Open Food Facts and Open Prices source policies. Both database sources are ODbL and require attribution/share-alike handling. The source shards are data, not application source code, and the repository software license must not be presented as restricting rights granted by those separately licensed database works.

`HalalFoodEU/Resources/catalog.sqlite3` and `catalog-manifest.json` are generated artifacts and remain ignored by Git. The application bundle receives those generated resources; it does not parse CSV at runtime. The generated catalog manifest is the release authority for the resulting database and records catalog/source identity, attribution/licenses, generation metadata, schema/version, counts, digest, and the bound source-shard-set identity.

Synthetic demonstration products remain fixture-only. CI generates a separate production-shaped synthetic catalog under `HalalFoodEUTests/Resources` so application tests do not depend on the mutable real catalog. Synthetic fixtures do not describe real retail products and are governed by the repository software license.

## Source admission rules

Every imported source must be reviewed before ingestion for:

1. permission to collect the data;
2. permission to redistribute it in this public repository and inside an application bundle;
3. attribution requirements;
4. database-right and share-alike obligations in the European Union;
5. compatibility with every other source combined in the same catalog; and
6. whether images, trademarks, ingredients, and database contents have different licenses.

Open Food Facts states that its database is available under the Open Database License 1.0 (ODbL), with attribution and share-alike requirements. Any catalog derived from that database must identify the applicable ODbL terms and attribution in its manifest and distribution. The application source license must not be presented as restricting rights that the ODbL grants in that separately licensed database.

Open Prices is likewise admitted through its reviewed ODbL source policy and is retained with observational retailer semantics; an observation does not imply current or nationwide retailer stock/completeness.

Retailer websites and applications must not be scraped or republished merely because their pages are publicly visible. Private retailer research masters may contain evidence useful for discovery/verification, but those rows remain outside public Git and the app bundle until a reviewed source mechanism explicitly permits the intended collection and redistribution. The same rule applies to manufacturer/certifier material whose rights are not admitted for bundle redistribution.

## Required manifest fields

A production catalog is not releasable unless its manifest includes the existing production evidence/rights contract, including:

- `catalogVersion`
- `schemaVersion`
- `generatedAt`
- `recordCount`
- `sha256`
- source identities/policies and applicable licenses/attributions
- methodology/selection/quality identity required by the production compiler
- source snapshot/retrieval lineage; and
- for Git-backed bundles, the bound `sourceShardSet` identity/logical digest/counts.

When source licenses are incompatible, build separate catalogs or omit the conflicting source. Do not erase provenance to make combination easier.
