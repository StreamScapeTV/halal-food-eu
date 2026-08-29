# 012 — Future roadmap

**Status:** Future; non-binding until promoted by a reviewed specification.  
**Last reviewed:** 2026-08-26

Potential capabilities are recorded here to keep the initial architecture extensible without prematurely implementing them.

## Catalog evolution

- Signed, compressed catalog delta downloads with atomic rollback and an offline bundled fallback.
- Background freshness checks that remain optional and privacy-preserving.
- Country/market-specific formulations sharing a GTIN with explicit conflict handling.
- Multiple compatible source catalogs kept separate when licenses cannot be combined.
- Reviewer tooling, change queues, double review for high-risk assessment changes, and audit exports.

## Product discovery

- Indexed local search and filters.
- Alternatives when a product is not halal/questionable, based on explicit catalog relationships rather than advertising.
- Product/category browsing.
- Current-catalog comparison for saved/history items.
- App Intents and Spotlight shortcuts for manual barcode lookup, subject to privacy review.

## Evidence capture

- On-device package-photo ingredient OCR with original image opt-in, confidence display, and explicit source licensing/consent.
- On-device language detection and labeled translation while preserving original text.
- GS1 Digital Link enrichment.
- Certificate-document verification and expiry tracking with certification-body partnerships.
- Structured user correction exports or submissions, with anti-abuse and moderation design.

## Personal features

- Local favorites and scan history.
- Local dietary preferences, including jurisprudential interpretation profiles, only after qualified methodology review.
- Optional encrypted sync using an Apple-native mechanism, with explicit consent and deletion.
- Widgets or Live Activities only where they provide meaningful scan/result value.

## Explicit prerequisites

No future item becomes implementation work until an accepted specification defines data flow, licensing, privacy, security, accessibility, offline behavior, failure modes, test strategy, and how it avoids overstating halal certainty.
