# 001 — Product vision and scope

**Status:** Accepted  
**Last reviewed:** 2026-08-26

## Vision

A person in Europe should be able to point an iPhone at a packaged-food barcode and receive a quick, offline, evidence-based explanation of whether the exact cataloged formulation is halal, not halal, questionable, or unknown—and how recent that evidence is.

## Users

- Muslims checking food products while shopping or at home.
- Parents or carers making household purchasing decisions.
- Reviewers maintaining product ingredients and evidence.
- Contributors building lawful, attributed catalog releases.

The application does not require an account and does not assume one jurisprudential school, certification body, country, or language represents every user.

## Accepted requirements

- **HF-PRODUCT-001:** The shipping product is an iPhone application. It must not add tvOS, macOS, Android, web, or server targets without a new accepted specification.
- **HF-PRODUCT-002:** The minimum deployment target is iOS 18.0.
- **HF-PRODUCT-003:** Core scanning, barcode normalization, product lookup, result explanation, and source/freshness display must work with networking disabled.
- **HF-PRODUCT-004:** The app must not require registration, login, subscription, advertising consent, or an analytics identifier.
- **HF-PRODUCT-005:** A successful lookup must identify the exact normalized GTIN/barcode used for the lookup and the catalog version that supplied the record.
- **HF-PRODUCT-006:** The primary result must be understandable within one screen, while evidence details remain inspectable.
- **HF-PRODUCT-007:** The app must distinguish a current recognized certification from a non-certified ingredient review.
- **HF-PRODUCT-008:** The app must show `questionable` or `unknown` rather than infer halal when evidence is incomplete, ambiguous, conflicting, or missing.
- **HF-PRODUCT-009:** Product formulation age must be visible. A positive assessment based on stale ingredients must carry a freshness warning.
- **HF-PRODUCT-010:** Product data and assessment decisions must be versioned independently from application source code.
- **HF-PRODUCT-011:** All real-product records must carry lawful source and redistribution metadata.
- **HF-PRODUCT-012:** The app must state that it is an informational evidence tool, not a fatwa, allergy guarantee, or substitute for checking current packaging and trusted authorities.

## Initial release boundary

The initial production slice consists of:

1. camera scan and manual barcode entry;
2. local lookup in the bundled catalog;
3. not-found handling;
4. product identity, ingredients, assessment, reason list, source, and dates;
5. catalog version/integrity information; and
6. accessible native UI.

Search, favorites, scan history, user submissions, and delta catalog updates are specified but may be delivered after the initial slice if their requirements remain respected.

## Explicit non-goals

- Issuing religious rulings for every user or school of thought.
- Claiming certification without evidence from a named certification source.
- Inferring animal source for ambiguous additives without evidence.
- Guaranteeing absence of cross-contamination, processing aids, alcohol traces, allergens, or supply-chain changes.
- Scraping public retailer pages without redistribution permission.
- Depending on a live remote API at scan time.
- Monetization, ads, tracking, or a hosted clone in the current repository license.
