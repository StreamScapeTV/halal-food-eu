# 027 — Native app shell and local settings

**Status:** Accepted  
**Last reviewed:** 2026-09-05

## Top-level application shell

- **HF-SHELL-001:** The iPhone app has exactly three top-level SwiftUI `TabView` destinations in this order: **Check**, **Saved**, and **Settings**. Standard system tab/navigation components are used; the app does not implement a custom tab bar or decorative Liquid Glass substitute.
- **HF-SHELL-002:** Check, Saved, and Settings each keep independent navigation-stack state while sharing the long-lived application dependencies created at launch. Switching tabs must not recreate the scanner, product-search, local-library, OCR, or evidence-submission models.
- **HF-SHELL-003:** Check remains the entry point for barcode camera/manual lookup, offline product search, ingredient OCR, and product/evidence presentation. The shell does not change the lookup, evidence, assessment, OCR, or submission contracts.
- **HF-SHELL-004:** Saved reuses the specification-006 favorites/history view model and the same separate writable local store already used by product results. Settings may route the user to the Saved tab, but it must not duplicate history/favorite destructive controls or persistence.

## Appearance preference

- **HF-SETTINGS-001:** Settings offers exactly **System**, **Light**, and **Dark** appearance choices. System is the default. The selected value is applied through SwiftUI system color-scheme APIs and never changes product evidence, status, reason, freshness, or methodology meaning.
- **HF-SETTINGS-002:** Appearance is the only preference persisted by this surface. It is stored locally as a small enum value outside both `catalog.sqlite3` and the specification-006 history/favorites SQLite store. Missing or unrecognized persisted values fail safely to System.

## Language handoff

- **HF-SETTINGS-003:** The app does not implement a custom language override. The Settings language row explains that iOS controls the app language, states that English and German ship now, and opens the public `UIApplication.openSettingsURLString` destination so the user can manage the app in system settings.

## Privacy and local data

- **HF-SETTINGS-004:** Settings summarizes the existing privacy boundary without weakening specification 008: the product catalog is bundled/offline, core lookup does not require a network connection, ingredient OCR runs on device and is ephemeral, favorites/history are local-only, and the app has no account, analytics, advertising, or tracking system.
- **HF-SETTINGS-005:** Settings provides a clear route to Saved for managing favorites and optional scan history. It does not introduce a second erase/reset implementation.

## Runtime identity

- **HF-SETTINGS-006:** Settings displays the app marketing version, build number, and current bundled catalog version from local runtime identity. Missing identity fields are shown as unavailable; the surface does not fetch remote release or catalog metadata.

## Localization and accessibility

- **HF-SETTINGS-007:** All tab and Settings strings ship in English and German together. Copy is stored as complete localization-ready phrases rather than grammar-fragile fragments.
- **HF-SETTINGS-008:** Settings uses native controls that support Dynamic Type, VoiceOver, Bold Text, contrast, and system appearance. Actions that leave the current surface or switch tabs expose meaningful accessibility hints.

## Acceptance

- **HF-SETTINGS-009:** Automated tests cover System/Light/Dark behavior, default and invalid-value fallback, local persistence scope, exact tab composition and Saved routing, runtime identity normalization, iOS settings URL handoff, and EN/DE Settings resource parity. The existing scanner, search, OCR, evidence/submission, favorites/history, catalog, and accessibility suites remain green under the accepted Xcode/iOS baseline.
