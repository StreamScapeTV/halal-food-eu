# 005 — Data sourcing, provenance, and freshness

**Status:** Accepted  
**Last reviewed:** 2026-08-26

## Source priority

The preferred evidence order is:

1. exact current package label or manufacturer data for the relevant market;
2. current certificate or statement from a recognized certification body;
3. official manufacturer/retailer API or feed with redistribution permission;
4. an open product database with compatible attribution/share-alike terms;
5. a dated community submission with package evidence, pending review.

Priority is not absolute: a newer exact package can supersede an older manufacturer page, and certification scope must still match the product and market.

## Legal and provenance requirements

- **HF-DATA-001:** Public visibility is not a data license. No retailer or manufacturer page may be scraped and redistributed without terms or written permission allowing the intended use.
- **HF-DATA-002:** Every source must declare its identity, source type, reference/record ID, retrieval timestamp, applicable license/permission, and attribution text.
- **HF-DATA-003:** Source data with incompatible database licenses must not be merged into one distributed catalog.
- **HF-DATA-004:** A source’s ingredient text must be preserved verbatim with its language and a content hash before normalization or assessment.
- **HF-DATA-005:** Normalized ingredient tokens may supplement but never replace the original text.
- **HF-DATA-006:** Every ingredient observation must record `observedAt` when the formulation was seen/declared and `retrievedAt` when the project acquired it. When only retrieval time is known, that limitation must be represented.
- **HF-DATA-007:** Importing a changed ingredient hash creates a new observation and clears inherited assessment status until reviewed.
- **HF-DATA-008:** Real product names, brands, images, trademarks, ingredient text, and database records may have different rights; the pipeline must track them independently where required.
- **HF-DATA-009:** Production imports must retain enough raw source reference to audit the record without storing prohibited personal data.
- **HF-DATA-010:** Machine translation or OCR must record tool/version and confidence and must not silently replace the source text.

## Freshness

- **HF-FRESH-001:** The default formulation freshness threshold is 12 months from `observedAt`; the methodology may define a shorter category/source threshold.
- **HF-FRESH-002:** At 9 months, a record becomes `refresh-recommended`; after 12 months it becomes `stale` for UI purposes.
- **HF-FRESH-003:** Freshness does not automatically rewrite historical status, but stale positive results must show a prominent warning before reasons.
- **HF-FRESH-004:** A source-provided “last modified” date may supplement but not replace the observation/retrieval dates.
- **HF-FRESH-005:** The app must show absolute dates, not only relative phrases such as “last year.”

## Open Food Facts boundary

Open Food Facts is a candidate source because it provides an open database, but its ODbL attribution and share-alike obligations apply to derived databases. A catalog combining Open Food Facts data with another source must be legally compatible and distributable under the required terms. The source code license remains separate from the database license.

## Retailer boundary

Lidl, REWE, EDEKA, Aldi, dm, Rossmann, Kaufland, Carrefour, Tesco, and other retailer data are not approved merely by naming them here. Each source needs a recorded API/feed/permission review before ingestion.
