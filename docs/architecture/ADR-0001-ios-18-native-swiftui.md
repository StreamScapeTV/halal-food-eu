# ADR-0001 — iOS 18 minimum and native SwiftUI

**Status:** Accepted  
**Date:** 2026-08-26

## Context

The app is iPhone-only. It should support iOS 18 while adopting the current Apple design automatically on iOS 26 when built with the current stable Xcode 26 toolchain. Maintaining hand-built parallel “glass” and “non-glass” themes would add complexity and age poorly.

## Decision

- Set the deployment target to iOS 18.0.
- Build in Swift 6 language mode with complete strict concurrency checking.
- Use SwiftUI standard controls and navigation as the default UI vocabulary.
- Build with stable Xcode 26 in CI.
- Do not opt out of the current system design.
- Do not emulate Liquid Glass on iOS 18. Availability-gate only genuinely needed iOS 26 APIs.
- Use VisionKit `DataScannerViewController` through a narrow SwiftUI adapter for camera scanning, with manual entry as a complete fallback.

## Consequences

Standard controls receive the appropriate system appearance for the running OS. The codebase remains one UI implementation, iOS 18 users remain supported, and custom visual effects require justification rather than becoming an app-wide styling framework.
