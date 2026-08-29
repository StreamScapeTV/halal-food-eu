# ADR-0006 — Secure catalog ingestion and runtime integrity

**Status:** Accepted  
**Date:** 2026-08-29

## Context

Catalog material is hostile until it has crossed explicit validation boundaries. Future production sources can contain malformed structured data, decompression bombs, traversal paths, terminal or spreadsheet injection payloads, misleading URLs, oversized fields, prohibited image bytes, or content designed to consume excessive memory. The public GitHub repository also has a software-supply-chain boundary: pull requests are untrusted, workflow credentials must remain least privilege, and third-party tools/actions must not float.

The iOS application is offline and consumes an immutable bundled SQLite catalog. Build-time validation is necessary but not sufficient to distinguish a damaged or replaced bundle resource from an accepted catalog at runtime.

## Decision

1. `Tools/catalog_security.py` is the standard-library security primitive layer for source adapters and catalog tooling. It provides bounded strict-UTF-8 JSON/CSV parsing, safe HTTPS allowlisting, SSRF-sensitive resolved-address rejection, bounded redirect/response handling, bounded regular-file-only ZIP extraction, terminal/CSV output neutralization, secret-canary checks, and the current metadata-only product-image boundary.
2. Source adapters must opt into explicit hosts and path prefixes. Caller-supplied URLs, credentials, commands, refs, secret names, output paths, or executable fragments are never source policy.
3. Archive processing extracts only regular files beneath a dedicated root and rejects traversal, absolute paths, backslashes, symlinks/devices, encryption, excess entry counts, excess expanded bytes, and suspicious compression ratios.
4. Python catalog tooling remains standard-library-only. External GitHub Actions and XcodeGen are recorded in `Data/security/tooling-dependencies-v1.json`; actions use full commit SHAs and CI builds XcodeGen 2.46.0 from reviewed commit `8445e778451c7e44237b90281bde622d764b0084` using its committed SwiftPM resolution.
5. Every catalog manifest is bound after deterministic database creation to the exact reviewed source-policy schema/version and SHA-256. CI validates the binding before handoff/release evidence.
6. `SQLiteProductCatalog` treats both bundled files as untrusted inputs. Before serving a lookup it validates manifest structure, supported catalog/source-policy schemas, database SHA-256, read-only/query-only mode, application/schema IDs, `integrity_check`, `foreign_key_check`, required tables, catalog metadata, and manifest record count.
7. Security errors fail closed and avoid echoing raw hostile values or secret canaries. The catalog pipeline never converts parser success into halal meaning.
8. Security incidents follow `docs/security/catalog-incident-response.md`; compromised evidence is revoked and replaced through a reviewed last-known-good/new catalog release rather than silently patched in place.

## Consequences

Production source adapters have reusable guardrails but still own source-specific schemas, licenses, rate limits, timeouts, redirect handling, and review policy. Network code remains absent from the iOS runtime. The build becomes slightly more explicit because source-policy identity and reviewed tooling pins are testable release evidence. Runtime startup performs a bounded catalog digest and SQLite integrity verification on first lookup; subsequent lookups reuse the validated read-only connection.

Product images remain references only. A future requirement to download, OCR, or re-encode image bytes needs a separate accepted specification and tighter byte/pixel/metadata/privacy limits before admission.
