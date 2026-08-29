# Halal Food EU — Agent Instructions

## Operating model

This repository is intentionally maintained by one continuous implementation agent. There is no orchestrator, Agent State integration, cross-repository claim system, or multi-agent scheduling mechanism.

Use GitHub issues as the only durable progress tracker. Work on one mutable issue at a time, keep its checklist truthful, and open a pull request only after the issue branch is complete and locally/CI validated.

## Source of truth

`docs/feature-specs/**` is the canonical product and engineering authority. When code, issue text, comments, or assumptions conflict with an accepted feature specification, the feature specification wins. Any intentional behavior change must update the relevant specification in the same pull request.

Architecture decisions under `docs/architecture/**` explain implementation choices but may not override feature requirements.

GitHub priority/status semantics and autonomous issue selection are defined in `docs/governance/issues-and-priorities.md`; `.github/labels.json` is the managed-label source of truth.

## Permanent scope

- Product: Halal Food EU.
- Platform: iPhone/iOS only.
- Minimum deployment target: iOS 18.0.
- UI: native SwiftUI and standard Apple controls.
- Toolchain baseline: current stable Xcode 26 toolchain, Swift 6 language mode, complete strict concurrency checking.
- Core operation: barcode lookup must work offline from a versioned SQLite catalog bundled with the app.
- Excluded unless a future accepted specification changes scope: tvOS, macOS, Android, accounts, backend services, mandatory network access, analytics, advertising, and user tracking.

## Engineering rules

- Preserve separation between App composition, Domain models/use cases, Data implementations, and Feature UI.
- Depend on protocols at domain boundaries and inject concrete implementations at the composition root.
- Use factories only where construction varies or centralizing construction removes coupling; do not introduce ceremonial abstractions.
- Keep all UI state on `@MainActor`. Never perform database work on the main actor.
- Use structured concurrency, cancellation, immutable `Sendable` value types, parameterized SQL, prepared statements, and indexed lookup paths.
- The bundled catalog is read-only at runtime. Store user-owned mutable data in a separate store if such features are added later.
- Do not add a backend or make scanning depend on a network request.
- Keep third-party runtime dependencies at zero unless an accepted ADR demonstrates a material benefit that cannot reasonably be achieved with Apple frameworks.

## Halal-data integrity

- Never label a real product halal merely because no obviously prohibited ingredient was found.
- Preserve ingredient text, language, source, source record identifier, observation date, retrieval date, review date, methodology version, and reason-level evidence.
- Support honest `questionable` and `unknown` outcomes. Ambiguous ingredient origin, conflicting evidence, missing ingredients, or stale evidence must not be hidden.
- Treat each ingredient-list change as a new immutable observation; do not rewrite history.
- Do not scrape retailer sites or redistribute retailer data unless their API, license, terms, or written permission expressly allow the intended collection and redistribution.
- Keep software licensing separate from catalog-data licensing. Every catalog build must declare its data license and attribution in its manifest.

## Issue selection

1. Continue the sole `status:in-progress` issue when one exists and remains valid.
2. Otherwise select from `status:ready` issues in P0, then P1, then P2 order.
3. Within a priority, follow the dependency order in the master epic; otherwise prefer the lowest issue number.
4. Record blockers durably and transition the issue before selecting different work.
5. Never maintain more than one `status:in-progress` issue.
6. Never put secret values, private contracts, credentials, or personal data into public issue bodies.

## Delivery workflow

1. Read this file and the relevant feature specifications.
2. Create or select one GitHub issue and record the plan/checklist there.
3. Mark that issue `status:in-progress`, then branch from current `main` as `agent/<issue>-<slug>`.
4. Implement the full issue with tests and specification updates.
5. Run catalog validation and iOS build/tests on GitHub-hosted runners.
6. Review the diff for architecture, concurrency, privacy, licensing, data provenance, accessibility, and scope.
7. Set `status:review` and open a pull request only when the branch is merge-ready.
8. Merge after green CI, then review the exact integrated `main` result.
9. Only after post-merge validation, set `status:done` and close the issue.

Never claim completion when the corresponding issue checklist, tests, CI state, or post-merge verification says otherwise.
