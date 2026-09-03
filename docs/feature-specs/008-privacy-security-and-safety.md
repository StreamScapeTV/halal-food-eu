# 008 — Privacy, security, and safety

**Status:** Accepted  
**Last reviewed:** 2026-09-03

## Privacy

- **HF-PRIVACY-001:** Core app operation requires no account, advertising identifier, analytics SDK, tracking permission, contacts, location, microphone, broad photo-library permission, or health access. User-selected library photos may be accessed through the system Photos picker for the explicit evidence flow in specification 018.
- **HF-PRIVACY-002:** Camera access is requested only for barcode scanning, explicit ingredient OCR under specification 026, and explicit user-initiated package-evidence capture under specification 018. The camera is never used for background collection or analytics.
- **HF-PRIVACY-003:** Barcode-scanner camera frames are processed on device and are not saved or transmitted. An ingredient OCR image under specification 026 is processed locally and remains ephemeral by default. A package-evidence photo is retained only when the user explicitly captures/selects it for a submission; it is sanitized locally and remains local until the user deliberately invokes Mail/share/copy.
- **HF-PRIVACY-004:** Manual barcodes, scan results, OCR results, and errors are not sent to a server by the core app. Specification 018 permits only explicit user-directed package transport through system Mail/share surfaces; the project still operates no intake backend.
- **HF-PRIVACY-005:** Any network, diagnostics, feedback, or sync capability requires an accepted specification identifying data fields, purpose, retention, consent/deletion boundaries, and failure behavior. Specification 018 is the accepted contract for backend-free product evidence submission; specification 026 explicitly authorizes local-only OCR and no transport.
- **HF-PRIVACY-006:** The privacy manifest must truthfully declare tracking, collected data, and required-reason API use. User-directed transport in another system app must not be misrepresented as project analytics/tracking collection.
- **HF-PRIVACY-007:** Ingredient OCR must not write captured image bytes or recognized text to SQLite, scan history, analytics, logs, caches intentionally owned by the app, or a network destination. Explicit copy to the system pasteboard is the only initial export action.

## Catalog and application security

- **HF-SEC-001:** SQL values are bound parameters. Barcode text is never interpolated into SQL.
- **HF-SEC-002:** The bundled database is opened read-only and treated as untrusted until build-time integrity validation passes.
- **HF-SEC-003:** Catalog release validation includes SHA-256, SQLite `integrity_check`, foreign-key validation, schema/application IDs, status/reason constraints, date parsing, provenance/license presence, and indexed lookup plans.
- **HF-SEC-004:** The app must reject unsupported schema versions rather than attempting permissive reads.
- **HF-SEC-005:** Source URLs, when made tappable, must be limited to safe schemes and displayed before navigation.
- **HF-SEC-006:** No API key, signing credential, certificate, private feed token, or user data is committed to Git.
- **HF-SEC-007:** Dependencies are minimized and pinned/reviewed when introduced.
- **HF-SEC-008:** OCR image input is hostile input: byte size, source dimensions, decoded pixel count, decode success, and processing bounds are validated before Vision receives the image.

## Religious and consumer safety

- **HF-SAFETY-001:** The app must not present an ingredient-only review as an official certificate.
- **HF-SAFETY-002:** The app must not make allergy, medical, nutritional, or cross-contamination guarantees.
- **HF-SAFETY-003:** The app must show source and date near enough to the result that a user can assess currency.
- **HF-SAFETY-004:** A stale, ambiguous, or conflicting record must not use reassuring language that hides the limitation.
- **HF-SAFETY-005:** Users must be encouraged to check current packaging/manufacturer/certifier evidence for consequential decisions.
- **HF-SAFETY-006:** Methodology changes must not retroactively alter stored historical review meaning without versioning and re-review.
- **HF-SAFETY-007:** OCR confidence is never a religious/evidence confidence score. OCR-only text cannot create or upgrade a halal status.
