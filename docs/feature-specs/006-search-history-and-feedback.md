# 006 — Search, history, and feedback

**Status:** Accepted  
**Last reviewed:** 2026-09-03

## Product search

- **HF-SEARCH-001:** Search is local and must not require a network request.
- **HF-SEARCH-002:** Search may match normalized product name, brand, and barcode, with language-aware tokenization where available.
- **HF-SEARCH-003:** Product-name search results must not be treated as an exact barcode identity until the user selects a record.
- **HF-SEARCH-004:** The SQLite implementation must use indexes/FTS and return bounded pages; no unbounded `%query%` table scan is allowed.
- **HF-SEARCH-005:** Filters may include status, evidence freshness, brand, and country/market only when the catalog has reliable data for the field.

## Scan history and favorites

- **HF-HISTORY-001:** History and favorites are opt-in local user data and are stored in a separate writable application-data store, never as mutable tables in the read-only bundled catalog.
  - Scan history is **off by default**.
  - Favorites are explicit user actions and remain independent of the scan-history setting; favoriting a product must not enable history.
- **HF-HISTORY-002:** The user can clear all history and remove individual history entries. Favorites can also be removed individually. Disabling future history does not silently delete existing entries; clear/delete remain explicit user actions.
- **HF-HISTORY-003:** History stores only a valid camera-scanned canonical GTIN, scan time, the catalog version viewed at that time, and a bounded versioned product-comparison fingerprint. It never stores camera imagery, OCR imagery/text, a full stale `ProductRecord`, device location, manual barcode lookups, product-search selections, demo lookups, or correction/submission payloads.
  - A retry of a resolved camera scan is a lookup action, not a second scan event.
  - History retains at most the newest **200** scan entries on device.
- **HF-HISTORY-004:** Opening an old history/favorite item resolves its GTIN through the current exact bundled `ProductCatalog` path. The UI distinguishes:
  - the catalog version changed but the exact product-level marker is unchanged;
  - the current product record materially changed;
  - a previously present product is no longer in the current catalog; and
  - a previously missing GTIN now has a current record.
  The comparison marker is a versioned SHA-256 fingerprint over bounded runtime identity/evidence/assessment/retailer semantics and excludes remote image references; a global catalog-version change alone must not be presented as a product change.
- **HF-HISTORY-005:** No history, favorite, or dietary profile leaves the device without a future explicit sync specification and consent.
  - The local history/favorites store is excluded from device cloud backup/sync by the app.
  - No account, analytics, telemetry, network upload, or iCloud/CloudKit synchronization is introduced by local history/favorites.
  - Local persistence failure is reported separately and must never block exact barcode lookup, search, OCR, or evidence display.

## Not-found and correction feedback

Without a backend, the app may prepare the explicit user-directed product evidence package defined by specification 018. The report can include the GTIN, catalog version, user-entered product/retailer context, dated package evidence, consent, and only the photos the user deliberately selected or captured for the submission.

- **HF-FEEDBACK-001:** A not-found/correction report must never automatically attach a photo, device location, account identity, scan history, or camera scanner frame. Package photos are included only after explicit user selection/capture under specification 018.
- **HF-FEEDBACK-002:** User corrections are untrusted submissions until a reviewer verifies source rights, package evidence, dates, privacy, identity and methodology.
- **HF-FEEDBACK-003:** A correction must create a new observation through the reviewed intake path rather than silently mutating historical evidence or the bundled read-only catalog.
- **HF-FEEDBACK-004:** The app must explain that submitting evidence does not guarantee inclusion or a particular assessment.
- **HF-FEEDBACK-005:** Mail/share/copy is an explicit transport boundary. Composer completion does not mean the project received or accepted the evidence.
