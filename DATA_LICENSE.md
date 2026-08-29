# Catalog data licensing and attribution

Software and catalog data are different works and do not automatically share a license.

## Current bundled demonstration catalog

`HalalFoodEU/Resources/catalog.sqlite3` and `Data/sample-products.json` currently contain only synthetic demonstration records created for this repository. They do **not** describe real retail products and are governed by the repository `LICENSE`.

The catalog manifest is authoritative for every generated database. It records the catalog version, source set, attribution, data license, generation timestamp, schema version, record count, and SHA-256 digest.

## Future real-product catalogs

Every imported source must be reviewed before ingestion for:

1. permission to collect the data;
2. permission to redistribute it inside an application bundle;
3. attribution requirements;
4. database-right and share-alike obligations in the European Union;
5. compatibility with every other source combined in the same catalog; and
6. whether images, trademarks, ingredients, and database contents have different licenses.

Open Food Facts states that its database is available under the Open Database License 1.0 (ODbL), with attribution and share-alike requirements. Any catalog derived from that database must identify the applicable ODbL terms and attribution in its manifest and distribution. The application source license must not be presented as restricting rights that the ODbL grants in that separately licensed database.

Retailer websites and applications must not be scraped merely because their pages are publicly visible. Data from Lidl, REWE, EDEKA, or another retailer may be imported only through an official feed/API or permission whose terms allow the intended storage and redistribution.

## Required manifest fields

A production catalog is not releasable unless its manifest includes:

- `catalogVersion`
- `schemaVersion`
- `generatedAt`
- `recordCount`
- `sha256`
- `dataLicense`
- `attribution`
- `sources[]` with source identity, source-record reference, retrieval date, and applicable license
- `methodologyVersion`

When source licenses are incompatible, build separate catalogs or omit the conflicting source. Do not erase provenance to make combination easier.
