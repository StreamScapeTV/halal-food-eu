# Contributing

Halal Food EU accepts focused contributions through GitHub issues and pull requests. The project does not use an orchestrator or external agent-state system.

## Before changing code

1. Read `AGENTS.md` and the relevant accepted specifications in `docs/feature-specs/`.
2. Open or select one issue and describe the intended behavior, acceptance criteria, data/license impact, and tests.
3. Confirm that any proposed product data can legally be collected and redistributed.
4. Branch as `agent/<issue>-<slug>` or `contrib/<issue>-<slug>`.

## Pull-request standard

A pull request must be merge-ready when opened. It must:

- resolve one issue or a coherent bounded slice;
- update feature specifications when behavior changes;
- preserve iOS 18 compatibility and native SwiftUI behavior;
- pass catalog validation, unit tests, integration tests, and the iOS simulator build;
- avoid new runtime dependencies unless an accepted ADR justifies them;
- include provenance and licensing for every added data record;
- never weaken `questionable`/`unknown` handling merely to produce more positive results; and
- retain the repository license and notices.

## Developer Certificate of Origin

By contributing, you certify that you created the contribution or have the right to submit it, and that it may be licensed as described in Section 4 of `LICENSE`. Sign commits with:

```text
Signed-off-by: Your Name <your-email@example.com>
```

The repository owner may request a separate contributor agreement for substantial contributions.
