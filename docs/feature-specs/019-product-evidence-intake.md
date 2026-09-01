# 019 — Owner-admitted product evidence intake and local OCR

**Status:** Accepted  
**Last reviewed:** 2026-09-01

## Purpose and boundary

Product-evidence mail/share packages from specification 018 are untrusted transport artifacts. They never become catalog evidence merely because a message was sent or a file exists. The owner/reviewer first screens the material outside automated catalog admission, then a bounded local/trusted workflow validates the deliberately admitted package, re-encodes images, produces assistive OCR, and—only after explicit human verification—materializes canonical immutable evidence records as a normal reviewed Git patch/PR candidate.

The repository has no mailbox integration, email OAuth credential, upload backend, or cloud OCR dependency.

## Owner admission

- **HF-INTAKE-001:** The canonical user-side payload remains `product-evidence-submission-v1`. Intake adds a separate versioned owner-admission record; it never rewrites user consent into reviewer approval.
- **HF-INTAKE-002:** Before automated processing, an owner/reviewer confirms sender identity/raw email are excluded, unrelated personal information is removed, location metadata is removed or rendered irrelevant by re-encoding, package rights/permission permit project review, and unexpected mailbox artifacts/files are excluded.
- **HF-INTAKE-003:** Reviewer identities use `github:<login>` and trusted workflow runs bind admission and final review to the authenticated GitHub actor. A different actor cannot silently finalize another reviewer’s declaration.
- **HF-INTAKE-004:** A public repository branch is public storage even if short-lived or later deleted. The trusted GitHub workflow therefore requires explicit `publicRepositoryStagingApproved: true`. If public staging is not approved, the same intake/OCR/finalization tooling may run locally in private mode and image bytes must remain outside public Git.
- **HF-INTAKE-005:** Raw email archives, sender addresses, mailbox headers/tokens, receipts, faces, addresses, payment data, credentials, geolocation and unrelated personal information are never valid catalog inputs.

## Hostile package validation

- **HF-INTAKE-006:** Package directories are closed: exactly `submission.json`, `admission.json`, and the declared stable JPEG filenames are allowed. Symlinked roots/files, nested directories, path traversal, undeclared files and non-regular files fail closed.
- **HF-INTAKE-007:** The validator independently verifies GTIN check digit, market, observation/consent/admission dates, issue-specific required image purposes, counts, declared byte/dimension bounds, exact filenames, SHA-256 values and reviewer admission state.
- **HF-INTAKE-008:** Input must be bounded single JPEG content. Non-JPEG files, trailing payload/polyglot bytes, duplicate image bytes, malformed marker streams, unsafe dimensions, too-small review images, oversized bytes and decoded-pixel bombs fail locally before OCR.
- **HF-INTAKE-009:** The trusted workflow rechecks the validated input hash immediately before Apple decoding. ImageIO decodes one image with bounded dimensions/pixels, applies orientation, and re-encodes a new JPEG from pixel data without copying source EXIF/GPS/XMP dictionaries. Re-encoded bytes/dimensions/hashes are recorded separately from input hashes.
- **HF-INTAKE-010:** Duplicate admission defense is versioned in `admitted-submission-registry-v1`: previously admitted submission IDs, original input attachment hashes, and admitted re-encoded hashes cannot be silently reused by another submission.

## Assistive local OCR

- **HF-INTAKE-011:** Initial OCR uses Apple Vision `VNRecognizeTextRequest` locally on the macOS runner or local Mac; no cloud OCR service/token is required.
- **HF-INTAKE-012:** OCR runs only for explicit ingredient-panel purposes. Barcode/front/certification/nutrition images may be re-encoded but are not text-transcribed by the automated intake tool.
- **HF-INTAKE-013:** Initial recognition hints are German (`de-DE`) and English (`en-US`) when supported by the installed Vision revision. The report records the Vision request revision, input and admitted image hashes, languages, text lines, confidence and normalized bounding boxes.
- **HF-INTAKE-014:** Vision language correction is disabled for evidentiary transcription. OCR output is always `unverified`; empty/unreadable output stays unreadable instead of inventing ingredients. OCR never translates or normalizes the source text into accepted evidence.
- **HF-INTAKE-015:** Reproducibility means the exact image hashes, engine/revision, language hints and per-region output are recorded so a reviewer can reproduce/compare the assistive run. The generated timestamp is audit metadata, not evidence freshness.

## Human verification and canonical evidence

- **HF-INTAKE-016:** A human proposal review explicitly confirms barcode-to-GTIN match, product/variant/quantity identity, market, ingredient panels when present, privacy, and rights. Every confirmation must be true before a proposal can be materialized.
- **HF-INTAKE-017:** Human-verified ingredient text is entered separately from OCR and must reference every and only submitted ingredient-panel image. It becomes canonical `ingredientsText` with `captureMethod: package-transcription` and `verificationState: human-verified` under evidence specification 014.
- **HF-INTAKE-018:** Human-verified package image hashes become canonical `packageEvidence` records; catalog/runtime data stores hashes/internal references, never admitted image binaries. Identity and package/ingredient review records are deterministic immutable evidence IDs.
- **HF-INTAKE-019:** The proposal creates no assessment and carries no submitter/sender accepted halal verdict. A new/changed formulation or status/certification concern is routed to methodology review under specification 013/#11. Where current correction context exists, current assessment invalidation is mandatory before any former positive status can remain current.
- **HF-INTAKE-020:** Submitter notes may be retained only after identity removal. Final-review text containing email-, URL-, or long phone-like contact data fails the intake validator instead of entering the proposal.

## Git and workflow lifecycle

- **HF-INTAKE-021:** Production intake is `workflow_dispatch` only, runs from trusted `main` workflow code, uses `contents: read`, receives no acquisition/source credentials, and never pushes or directly mutates a Git branch, SQLite catalog or assessment.
- **HF-INTAKE-022:** The first trusted run emits a seven-day artifact containing only the workflow-re-encoded images, validation report, unverified OCR report and human-review guide. It never uploads the raw email or unvalidated mailbox files.
- **HF-INTAKE-023:** After a reviewer supplies a valid human-review record, finalization emits a non-image proposal plus a deterministic Git patch bundle containing only `Data/submissions/admitted/<submission-id>.json` and the updated admitted-submission registry. The patch manifest explicitly states that a normal reviewed Git PR is still required.
- **HF-INTAKE-024:** The committed admitted proposal is durable non-personal audit input. Rejected/raw/private material has zero repository retention; short-lived review artifacts use seven-day retention. Deleting a public branch is never described as privacy erasure because public Git objects may have been fetched/cached.
- **HF-INTAKE-025:** Upstream Open Food Facts contribution is not part of initial admission. Future write credentials, if ever approved, belong to a separate explicit reviewed action and are never required for local catalog admission.

## Failure and security behavior

- **HF-INTAKE-026:** No submitted content is executed, sourced, unarchived, rendered as HTML/SVG, or used to construct arbitrary network requests. The intake only reads closed JSON plus declared JPEG files.
- **HF-INTAKE-027:** Partial validation/OCR failure produces no canonical evidence proposal. A rejected human decision cannot be finalized into evidence.
- **HF-INTAKE-028:** The immutable evidence validator from specification 014 must accept every generated identity, ingredient, package-evidence and review record before a patch bundle is emitted.
- **HF-INTAKE-029:** Registry/proposal digests and file hashes are recomputed during patch materialization; tampering between human review and Git proposal generation fails closed.

## Acceptance tests

Tests cover valid not-found/correction packages; malformed schema/GTIN/date/consent; public-vs-local staging; authenticated reviewer binding; path traversal/symlink/nested/unexpected files; executable/polyglot/trailing payloads; image count/byte/dimension/pixel/hash limits; duplicate submissions/images; German/English Vision OCR; unreadable ingredient images; OCR remaining unverified; human transcription coverage; PII-like final-review text; deterministic evidence compatibility; assessment invalidation/review routing; rejected submissions; registry idempotency; non-image patch bundles; least-privilege workflow configuration; and seven-day review-artifact retention.
