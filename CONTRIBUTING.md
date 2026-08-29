# Contributing

Halal Food EU accepts focused contributions through GitHub issues and pull requests. The project does not use an orchestrator or external agent-state system.

## Before changing code

1. Read `AGENTS.md`, `docs/governance/issues-and-priorities.md`, and the relevant accepted specifications in `docs/feature-specs/`.
2. Open or select one issue and describe the intended behavior, acceptance criteria, dependencies, source/data impact, and tests.
3. Confirm that any proposed real product data has an identifiable source and can be used for the intended catalog stage; never paste credentials or private source material into a public issue.
4. Ensure the issue has exactly one priority label and one status label. Move it to `status:in-progress` only when implementation starts.
5. Branch as `agent/<issue>-<slug>` or `contrib/<issue>-<slug>`.

Use the structured issue forms for implementation tasks, source proposals, product corrections, not-found products, and catalog incidents. Public issues must not contain API keys, passwords, tokens, private keys, private contracts, personal addresses, receipts, payment data, or unrelated personal information.

## Pull-request standard

A pull request must be merge-ready when opened. It must:

- resolve one issue or a coherent bounded slice;
- update feature specifications when behavior changes;
- preserve iOS 18 compatibility and native SwiftUI behavior;
- pass catalog validation, unit tests, integration tests, and the iOS simulator build;
- avoid new runtime dependencies unless an accepted ADR justifies them;
- preserve provenance/source metadata for every added real-data record;
- never weaken `questionable`/`unknown` handling merely to produce more positive results; and
- retain the repository license and notices.

After merge, verify the exact integrated `main` commit and required workflows before closing the issue as `status:done`.

## Developer Certificate of Origin

By contributing, you certify that you created the contribution or have the right to submit it, and that it may be licensed as described in Section 4 of `LICENSE`. Sign commits with:

```text
Signed-off-by: Your Name <your-email@example.com>
```

The repository owner may request a separate contributor agreement for substantial contributions.
