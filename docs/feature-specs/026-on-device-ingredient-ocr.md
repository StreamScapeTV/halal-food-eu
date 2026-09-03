# 026 — On-device ingredient OCR

**Status:** Accepted  
**Last reviewed:** 2026-09-03

## Purpose

A user may photograph the ingredient panel on a packaged food and obtain editable text without sending the image or text to a server. OCR is an assistive reading/transcription feature. It does not create catalog evidence, certification, or a halal assessment.

This feature is deliberately independent of retailer catalog coverage. Development and acceptance use deterministic synthetic fixtures; production catalog breadth may change without changing this OCR contract.

## Capture and recognition

- **HF-OCR-001:** Ingredient OCR is an explicit user-initiated camera flow. Generic file/photo-library import is outside the initial scope.
- **HF-OCR-002:** Recognition runs entirely on device using the Swift-native Vision `RecognizeTextRequest` available from iOS 18.
- **HF-OCR-003:** The request uses `.accurate` recognition and `usesLanguageCorrection = false` so package spellings, additive codes, punctuation, and uncertain text are not silently rewritten by a language model.
- **HF-OCR-004:** German (`de-DE`) and English (`en-US`) are supplied in that priority order when the active Vision revision reports them as supported. If neither is supported, Vision may fall back to automatic language detection rather than failing the feature solely on the hint list.
- **HF-OCR-005:** Input bytes are bounded before decoding. Source dimensions and decoded pixel count are bounded, orientation is normalized, and oversized images are downsampled before recognition.
- **HF-OCR-006:** Recognition returns immutable `Sendable` values containing the Vision request revision, effective language hints, line text, confidence, detected line languages, and normalized bounding boxes.
- **HF-OCR-007:** Recognized lines are projected into a deterministic top-to-bottom, then left-to-right reading order suitable for ordinary ingredient panels.

## User review and safety

- **HF-OCR-008:** Recognized text is shown in an editable review surface. The user must be able to correct OCR mistakes against the package.
- **HF-OCR-009:** OCR output is always labeled unverified. Confidence is diagnostic only and must never promote text to canonical product evidence or create `halal-certified`, `halal-reviewed`, `not-halal`, `questionable`, or `unknown` automatically.
- **HF-OCR-010:** Empty/unreadable recognition and processing failure are distinct recoverable states with a retry/capture-again path. The app must never invent fallback ingredient text.
- **HF-OCR-011:** The user may explicitly copy reviewed OCR text. No automatic import, upload, catalog mutation, evidence submission, or background processing is performed.

## Privacy and lifetime

- **HF-OCR-012:** Ingredient photos and OCR text are ephemeral by default. The OCR feature creates no scan history, analytics event, network request, or SQLite write.
- **HF-OCR-013:** Camera access is requested only after the user enters an explicit camera action. Camera-unavailable devices retain all barcode/manual catalog functionality; OCR itself may explain that camera capture is unavailable.
- **HF-OCR-014:** OCR work is isolated from `@MainActor`; UI state is `@MainActor`. Superseded or cancelled recognition must not publish stale results.

## Native UI, localization, and accessibility

- **HF-OCR-015:** The feature uses native SwiftUI/UIKit camera presentation and review controls with Dynamic Type, VoiceOver labels/hints, and no custom visual language that bypasses platform behavior.
- **HF-OCR-016:** English and German strings, including the camera usage description, ship with the feature. Recognized source text is never translated silently.

## Toolchain and dependencies

- **HF-OCR-017:** The accepted implementation uses the current stable Xcode 26 line. As of this review that is Xcode 26.6 (17F113) with the Swift 6.3 compiler; CI must select and verify that stable Xcode version while retaining Swift 6 language mode and complete strict concurrency.
- **HF-OCR-018:** XcodeGen remains on the current reviewed stable pin, 2.46.0, until a later reviewed toolchain change. No third-party OCR/runtime dependency is introduced.

## Acceptance

- **HF-OCR-019:** Unit tests cover success, empty text, errors, retry/reset behavior, cancellation/supersession, reading order, confidence aggregation, and unsafe input rejection.
- **HF-OCR-020:** The GitHub-hosted iOS lane executes at least one real Vision recognition smoke test against a deterministic synthetic ingredient-label image.
- **HF-OCR-021:** Existing barcode, bundled SQLite, evidence, submission, catalog-integrity, privacy, and security tests remain green.
