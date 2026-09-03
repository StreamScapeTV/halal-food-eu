# 012 — Future roadmap

**Status:** Future; non-binding until promoted by a reviewed specification.  
**Last reviewed:** 2026-09-03

Potential capabilities are recorded here to keep the initial architecture extensible without prematurely implementing them. Capabilities already promoted into accepted specifications are not future authority here: indexed offline product search is owned by specification 006, on-device ingredient OCR by specification 026, and local favorites/scan history/current-catalog saved-item comparison by specification 006.

## Catalog evolution

- Signed, compressed catalog delta downloads with atomic rollback and an offline bundled fallback.
- Background freshness checks that remain optional and privacy-preserving.
- Country/market-specific formulations sharing a GTIN with explicit conflict handling.
- Multiple compatible source catalogs kept separate when licenses cannot be combined.
- Reviewer tooling, change queues, double review for high-risk assessment changes, and audit exports.

## Product discovery

- Alternatives when a product is not halal/questionable, based on explicit catalog relationships rather than advertising.
- Product/category browsing.
- App Intents and Spotlight shortcuts for manual barcode lookup, subject to privacy review.

## Evidence capture

- On-device language detection and labeled translation while preserving original text.
- GS1 Digital Link enrichment.
- Certificate-document verification and expiry tracking with certification-body partnerships.
- Structured user correction exports or submissions beyond the accepted backend-free specification-018 transport, with anti-abuse and moderation design.

## Personal features

- Local dietary preferences, including jurisprudential interpretation profiles, only after qualified methodology review.
- Optional encrypted sync using an Apple-native mechanism, with explicit consent and deletion.
- Widgets or Live Activities only where they provide meaningful scan/result value.

## Explicit prerequisites

No future item becomes implementation work until an accepted specification defines data flow, licensing, privacy, security, accessibility, offline behavior, failure modes, test strategy, and how it avoids overstating halal certainty.
