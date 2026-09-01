# ADR-0010 — Owner-admitted product evidence with local Apple Vision OCR

**Status:** Accepted  
**Date:** 2026-09-01

## Context

Specification 018 lets an iPhone user prepare package evidence through Mail/share without a backend. Email transport is intentionally outside the project’s trust boundary. Issue #15 needs a reproducible way to turn deliberately screened package evidence into canonical immutable records without operating an upload service, exposing mailbox credentials, or trusting OCR as a halal decision.

The repository is public. A branch in this repository is therefore public storage even when called temporary and even after deletion. GitHub Actions artifacts also have retention/access semantics distinct from private local processing.

## Decision

1. **Owner admission precedes automation.** Raw email and first spam/privacy/rights screening happen outside the repository automation. The admitted package contains only the existing v1 submission, an owner-admission JSON record and declared JPEG package images.
2. **Public workflow staging is explicit.** The trusted GitHub workflow may process images only when the owner has explicitly approved public repository staging. Otherwise the same CLI/Apple tool is run locally and image bytes never enter public Git.
3. **Hostile bytes are checked twice.** A stdlib validator enforces closed paths, GTIN/dates, hashes, stable names, JPEG framing and resource bounds. Apple ImageIO then independently decodes one bounded image, applies orientation and re-encodes pixel data to a new JPEG without source metadata before OCR/review.
4. **Apple Vision is assistive only.** `VNRecognizeTextRequest` runs locally on macOS, initially with German/English hints and language correction disabled. Output records engine revision, hashes, confidence and bounding boxes and remains `unverified` regardless of confidence.
5. **Human transcription is authoritative for package ingredients.** A reviewer explicitly compares every ingredient panel and writes the final exact transcription. Generated canonical ingredient evidence uses `package-transcription` + `human-verified`, not OCR as an approved source.
6. **No automatic halal assessment exists.** The intake emits identity/package/ingredient/review records only. Corrections/new formulations carry an assessment-impact instruction that invalidates current assessment where applicable and routes material to methodology review. It never emits a positive/not-halal decision from submitter or OCR claims.
7. **Actions are read-only.** Production intake is manual dispatch from trusted `main`, `contents: read`, no source secrets and no pushes. After human verification it emits a non-image patch bundle; a normal reviewed branch/PR is still required to commit the proposal/registry update.
8. **Images are review-only, not catalog data.** Re-encoded image artifacts are retained for seven days. Durable Git data contains hashes/internal references and reviewed evidence proposal JSON only. Rejected/private/raw material has no repository retention.

## Alternatives rejected

- **Mailbox/API backend:** unnecessary operational/security/privacy surface for the initial path.
- **Cloud OCR:** adds credentials/data transfer and is unnecessary for initial German/English assistance.
- **Treating a public intake branch as private quarantine:** false privacy model; public Git is not ephemeral private storage.
- **Automatic OCR-to-catalog or OCR-to-halal status:** violates evidence/review invariants and creates unsafe false certainty.
- **Workflow push/direct catalog mutation:** bypasses normal PR review and separates final code/data state from review evidence.

## Consequences

The initial workflow is intentionally two-pass for public staging: generate review artifacts, then rerun with the human-review JSON to get a patch bundle. Private evidence can use the same tooling locally. This adds explicit reviewer work but preserves privacy, auditability and the repository’s immutable evidence/PR gates without introducing a backend.
