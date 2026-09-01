# 018 — Backend-free product evidence submission

**Status:** Accepted  
**Last reviewed:** 2026-09-01

## Purpose

A user who cannot find a packaged food, or who sees missing/stale/incorrect product evidence, can prepare owned package evidence on an iPhone without an account or project backend. Nothing leaves the device until the user explicitly reviews a native Mail composer, opens the share sheet, or copies the package details.

The canonical user-side v1 machine-readable contract is `Data/submissions/product-evidence-submission-v1.schema.json`. Repository-side intake, quarantine, OCR, reviewer admission, and catalog proposal remain owned by issue #15; receiving an email is not catalog admission.

## Entry points and identity

- **HF-SUBMIT-001:** A valid not-found GTIN offers `Submit product evidence`. Product results offer ingredient, identity, and certification/result correction actions, including when ingredients are missing or stale.
- **HF-SUBMIT-002:** The submission keeps the canonical GTIN-14, market, exact bundled catalog version, app version, issue type, and optional user-entered product/retailer context. Device location is never requested.
- **HF-SUBMIT-003:** One stable submission ID is generated when the submission view model is created and is reused for retries during that draft. The stable subject is `[Halal Food EU Product] <submission-id> <GTIN>`.
- **HF-SUBMIT-004:** Corrections may include the current catalog ingredient/source reference and dates needed to identify what is being challenged. The user package does not serialize the current halal assessment as accepted evidence and contains no submitter-provided accepted halal verdict.

## Package evidence

- **HF-SUBMIT-005:** Photo purposes are explicit: barcode, package front, ingredients, certification, and nutrition/variant. Required purposes depend on issue type; missing product requires barcode + front + ingredients, and other correction types require only the evidence necessary to review that concern.
- **HF-SUBMIT-006:** Photos are added only after a user selects or captures them. Scanner camera frames, scan history, account identity, location, and unrelated photos are never attached automatically.
- **HF-SUBMIT-007:** Selected/captured images are decoded with bounded dimensions/pixels, transformed for orientation, resized to a bounded review resolution, and re-encoded as JPEG without carrying source metadata. Unsafe, unreadable, tiny, oversized, multi-image, or over-budget inputs fail locally.
- **HF-SUBMIT-008:** A prepared attachment records stable filename, purpose, MIME type, dimensions, byte size and SHA-256 plus user ownership/privacy declarations. The app does not claim that these declarations are the owner/reviewer admission required by #15.
- **HF-SUBMIT-009:** Initial limits are eight photos maximum, 4,000,000 bytes per prepared JPEG, and 18,000,000 bytes total. Up to three ingredient-panel photos are supported for continuation panels.

## Structured envelope and trust boundary

- **HF-SUBMIT-010:** The JSON envelope uses `schemaVersion: 1` and `sourceType: user-package-evidence`, validates GTIN/market/date/text/attachment bounds, and records consent version/date. Its field set is closed by the canonical JSON Schema.
- **HF-SUBMIT-011:** The envelope is a user-side transport artifact, not an immutable admitted evidence record. #15 must independently inspect privacy/ownership, verify hashes/content, add reviewer admission, and convert accepted material to the canonical evidence model.
- **HF-SUBMIT-012:** Email receipt, Mail's `sent` result, a completed share sheet, or copied details never directly mutate the read-only SQLite catalog and never mean the evidence or a halal result was accepted.

## Consent and privacy

- **HF-SUBMIT-013:** Before package preparation the user confirms photo ownership/permission, package-evidence-only content, permission for project review/crop/redaction/use, separate redistribution review, and that submission guarantees neither catalog inclusion nor a halal outcome.
- **HF-SUBMIT-014:** The UI warns against credentials, receipts/payment data, faces, addresses, account details, and unrelated personal information. Optional retailer/city/store context is typed by the user.
- **HF-SUBMIT-015:** `PhotosPicker` is used for user-selected library images so the feature does not require broad photo-library access. User-initiated camera capture may offer the system editor/crop surface. The user is instructed to redact/crop existing photos before choosing them when needed.
- **HF-SUBMIT-016:** Submission drafts, images, GTINs, sender addresses, and composer outcomes are not logged or sent to analytics. The project still has no analytics/tracking backend.

## Native delivery and lifecycle

- **HF-SUBMIT-017:** The destination is the reviewed public `PRODUCT_SUBMISSION_EMAIL` from `Data/config/public-project-configuration-v1.json`, bundled as ordinary public configuration. No SMTP/API/OAuth secret is embedded.
- **HF-SUBMIT-018:** When Mail is configured, the app uses `MFMailComposeViewController` behind an injected composition boundary and prepopulates the reviewed recipient, stable subject, human-readable summary, machine-readable JSON, and sanitized images. The user can edit/review before pressing Send.
- **HF-SUBMIT-019:** When Mail is unavailable, Share package and Copy details/address remain available. A share sheet's completion can only be described as share-sheet completion; delivery cannot be confirmed.
- **HF-SUBMIT-020:** Temporary JSON/image files are created only for a prepared composer/share operation and are deleted on composer completion, cancellation, dismissal, retry replacement, or submission dismissal.
- **HF-SUBMIT-021:** User feedback distinguishes Mail `sent`, `cancelled`, and `failed/not available`. Even `sent` explicitly states that review/acceptance is not confirmed.

## Native UI, accessibility, and localization

- **HF-SUBMIT-022:** The flow uses native SwiftUI Form, Picker, DatePicker, PhotosPicker, sheets, toggles, buttons and standard UIKit wrappers only where required for Mail/share/camera. It adds no third-party runtime dependency.
- **HF-SUBMIT-023:** Required photo purposes have textual labels, progress/status is VoiceOver-readable, controls remain Dynamic-Type friendly, and no state is communicated by color alone.
- **HF-SUBMIT-024:** New user-facing submission strings are localization-ready with English development strings and German resources. Dates remain Foundation/SwiftUI formatted.

## Offline and security behavior

- **HF-SUBMIT-025:** Scanning, lookup, draft editing, photo sanitation, JSON generation, and package preparation work without a network request. Network transport happens only after the user explicitly asks another system app to send/share.
- **HF-SUBMIT-026:** The submission implementation has no write access to `ProductCatalog`; the bundled SQLite database remains read-only. No submission can update a product or assessment locally.
- **HF-SUBMIT-027:** The camera usage description truthfully covers barcode scanning and explicit package-evidence capture. The privacy manifest continues to declare no tracking/analytics collection by the app; transport chosen in Mail/share is user-directed system-app behavior.

## Acceptance tests

Tests cover exact subject/envelope fields, canonical GTIN/catalog version, required photo matrix, future-date and consent rejection, no halal-status/sender leakage, canonical public recipient, image size/metadata sanitation, package byte limits, temporary-file cleanup, Mail availability/fallback routing, correction/not-found contexts, German/English resource presence, and the absence of any catalog mutation path.
