# 009 — Performance and reliability

**Status:** Accepted  
**Last reviewed:** 2026-08-26

## Performance budgets

Measured on a supported release iPhone with a production-like catalog:

- **HF-PERF-001:** Exact indexed barcode lookup target is p95 under 50 ms warm and under 150 ms for the first lookup after database open.
- **HF-PERF-002:** Lookup must never execute on `@MainActor`; UI state updates return to `@MainActor` after awaited work.
- **HF-PERF-003:** The scanner must remain responsive while a lookup is in progress and must debounce duplicate detections.
- **HF-PERF-004:** No query may load the full catalog into memory for exact lookup.
- **HF-PERF-005:** Search results are paged and bounded; the initial page target is at most 50 rows.
- **HF-PERF-006:** Result rendering must avoid parsing or normalizing an entire catalog on the UI thread.
- **HF-PERF-007:** The baseline bundled catalog size target is below 250 MB; exceeding it requires an ADR covering install size, memory mapping, compression, and update strategy.

## Concurrency and cancellation

- **HF-CONCURRENCY-001:** Swift 6 complete strict concurrency checking is enabled.
- **HF-CONCURRENCY-002:** Domain values crossing isolation boundaries are immutable and `Sendable`.
- **HF-CONCURRENCY-003:** Database ownership is isolated behind one concurrency-safe component; raw SQLite handles/statements are not shared with UI code.
- **HF-CONCURRENCY-004:** Superseded lookups are cancelled or their results ignored.
- **HF-CONCURRENCY-005:** Tasks are structured and tied to feature/view-model lifetime; detached work requires explicit justification.

## Reliability

- **HF-RELIABILITY-001:** Missing database, corrupt database, invalid row, unsupported status, and schema mismatch have explicit error paths.
- **HF-RELIABILITY-002:** A database failure never becomes a normal not-found result.
- **HF-RELIABILITY-003:** Catalog build validation is deterministic at the logical-record level across SQLite library versions.
- **HF-RELIABILITY-004:** The app launches to a recoverable error explanation if the catalog cannot open; it must not crash-loop.
- **HF-RELIABILITY-005:** Integration tests exercise a real SQLite file using the same repository implementation as the app.
- **HF-RELIABILITY-006:** CI uses a concrete available iOS simulator selected at runtime rather than depending on one hard-coded device name.
