# 006 — Search, history, and feedback

**Status:** Accepted for behavior; delivery may follow the initial scanner slice.  
**Last reviewed:** 2026-08-26

## Product search

- **HF-SEARCH-001:** Search is local and must not require a network request.
- **HF-SEARCH-002:** Search may match normalized product name, brand, and barcode, with language-aware tokenization where available.
- **HF-SEARCH-003:** Product-name search results must not be treated as an exact barcode identity until the user selects a record.
- **HF-SEARCH-004:** The SQLite implementation must use indexes/FTS and return bounded pages; no unbounded `%query%` table scan is allowed.
- **HF-SEARCH-005:** Filters may include status, evidence freshness, brand, and country/market only when the catalog has reliable data for the field.

## Scan history and favorites

- **HF-HISTORY-001:** History and favorites are opt-in local user data and are stored separately from the read-only catalog.
- **HF-HISTORY-002:** The user can clear all history and remove individual entries.
- **HF-HISTORY-003:** History stores the scanned GTIN and time, not camera imagery.
- **HF-HISTORY-004:** Opening an old history item resolves it against the current bundled catalog and clearly indicates when the current record differs from the originally viewed catalog version.
- **HF-HISTORY-005:** No history, favorite, or dietary profile leaves the device without a future explicit sync specification and consent.

## Not-found and correction feedback

Without a backend, the initial app may offer a structured export/share action that creates a report containing the GTIN, optional user-entered product name, current catalog version, and fields the user elects to include.

- **HF-FEEDBACK-001:** A not-found report must never automatically attach a photo, location, account identity, or scan history.
- **HF-FEEDBACK-002:** User corrections are untrusted submissions until a reviewer verifies source rights, package evidence, dates, and methodology.
- **HF-FEEDBACK-003:** A correction must create a new observation rather than silently mutating historical evidence.
- **HF-FEEDBACK-004:** The app must explain that submitting a barcode does not guarantee inclusion or a particular assessment.
