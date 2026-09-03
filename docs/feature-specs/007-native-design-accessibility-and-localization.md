# 007 — Native design, accessibility, and localization

**Status:** Accepted  
**Last reviewed:** 2026-09-03

## Native platform design

- **HF-UI-001:** The app uses SwiftUI and standard navigation, toolbar, sheet, button, text field, list, form, alert, and content-unavailable components whenever they satisfy the interaction.
- **HF-UI-002:** Build with the current stable Xcode 26 toolchain while keeping `IPHONEOS_DEPLOYMENT_TARGET` at 18.0. As of 2026-09-03 the stable baseline is Xcode 26.6 (17F113); preview/beta Xcode 27 is not the production baseline.
- **HF-UI-003:** Standard controls must be allowed to adopt the system appearance: iOS 18 devices retain their native appearance and iOS 26 devices receive Liquid Glass behavior supplied by the system/toolchain.
- **HF-UI-004:** The app must not set the compatibility opt-out for the new design and must not imitate Liquid Glass with custom blur/card effects on older systems.
- **HF-UI-005:** Custom glass effects may be introduced only for a demonstrated interaction need and must be availability-gated; decorative glass is not a design goal.
- **HF-UI-006:** The primary flow is optimized for portrait iPhone use but must respond correctly to landscape, split accessibility sizes, and device safe areas unless a documented release limitation applies.
- **HF-UI-007:** Ingredient OCR uses native camera presentation and SwiftUI review controls. It must not introduce a custom camera/UI framework or a third-party OCR surface when platform APIs satisfy the requirement.

## Accessibility

- **HF-A11Y-001:** All text supports Dynamic Type without clipping or truncating status/reason meaning.
- **HF-A11Y-002:** Every icon-only control has an accessibility label and appropriate hint.
- **HF-A11Y-003:** Assessment status is communicated by text and symbol, never color alone.
- **HF-A11Y-004:** VoiceOver reading order follows status, summary, freshness, reasons, ingredients, source, and dates.
- **HF-A11Y-005:** Scanner guidance has a non-camera manual alternative.
- **HF-A11Y-006:** Controls meet Apple minimum target sizing and support Bold Text, Increase Contrast, Reduce Motion, and Reduce Transparency without loss of function.
- **HF-A11Y-007:** Animations and haptics are supportive, not required to understand success or failure.
- **HF-A11Y-008:** Ingredient OCR recognition, unreadable, failure, editable-text, and retry states expose meaningful VoiceOver labels and remain usable at accessibility text sizes.

## Localization

- **HF-L10N-001:** User-facing text must be localization-ready and must not be assembled from grammar-fragile fragments.
- **HF-L10N-002:** English is the development language. German is the first required production localization; French, Dutch, Spanish, Italian, Turkish, Arabic, Urdu, and other EU community languages are future candidates.
- **HF-L10N-003:** BCP-47 tags identify ingredient-language observations.
- **HF-L10N-004:** Right-to-left layout must use leading/trailing semantics and be tested before Arabic or Urdu ships.
- **HF-L10N-005:** Original ingredient text remains visible even when a translation is available, and translated text is labeled as translated.
- **HF-L10N-006:** Dates, numbers, and list formatting use locale-aware Foundation/SwiftUI formatters while stored timestamps remain UTC ISO-8601.
- **HF-L10N-007:** OCR text is preserved in its recognized/source form and is never silently translated. English and German OCR controls and camera-purpose text ship together.
