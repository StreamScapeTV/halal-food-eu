# 008 — Privacy, security, and safety

**Status:** Accepted  
**Last reviewed:** 2026-08-26

## Privacy

- **HF-PRIVACY-001:** Core app operation requires no account, advertising identifier, analytics SDK, tracking permission, contacts, location, microphone, photo-library, or health access.
- **HF-PRIVACY-002:** Camera access is requested only for barcode scanning.
- **HF-PRIVACY-003:** Camera frames are processed on device and are not saved or transmitted.
- **HF-PRIVACY-004:** Manual barcodes, scan results, and errors are not sent to a server by the core app.
- **HF-PRIVACY-005:** Any future network, diagnostics, feedback, or sync capability requires a specification identifying data fields, purpose, retention, legal basis, consent, deletion, and failure behavior.
- **HF-PRIVACY-006:** The privacy manifest must truthfully declare tracking, collected data, and required-reason API use.

## Catalog and application security

- **HF-SEC-001:** SQL values are bound parameters. Barcode text is never interpolated into SQL.
- **HF-SEC-002:** The bundled database is opened read-only and treated as untrusted until build-time integrity validation passes.
- **HF-SEC-003:** Catalog release validation includes SHA-256, SQLite `integrity_check`, foreign-key validation, schema/application IDs, status/reason constraints, date parsing, provenance/license presence, and indexed lookup plans.
- **HF-SEC-004:** The app must reject unsupported schema versions rather than attempting permissive reads.
- **HF-SEC-005:** Source URLs, when made tappable, must be limited to safe schemes and displayed before navigation.
- **HF-SEC-006:** No API key, signing credential, certificate, private feed token, or user data is committed to Git.
- **HF-SEC-007:** Dependencies are minimized and pinned/reviewed when introduced.

## Religious and consumer safety

- **HF-SAFETY-001:** The app must not present an ingredient-only review as an official certificate.
- **HF-SAFETY-002:** The app must not make allergy, medical, nutritional, or cross-contamination guarantees.
- **HF-SAFETY-003:** The app must show source and date near enough to the result that a user can assess currency.
- **HF-SAFETY-004:** A stale, ambiguous, or conflicting record must not use reassuring language that hides the limitation.
- **HF-SAFETY-005:** Users must be encouraged to check current packaging/manufacturer/certifier evidence for consequential decisions.
- **HF-SAFETY-006:** Methodology changes must not retroactively alter stored historical review meaning without versioning and re-review.
