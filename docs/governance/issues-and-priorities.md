# GitHub issue governance

GitHub Issues are the durable execution system for this single-agent repository. There is no orchestrator, external claim service, or hidden priority queue.

## Priority

- `priority:P0` — correctness, safety, security, data integrity, core architecture, or release blockers. Select a ready P0 before lower priorities.
- `priority:P1` — material production coverage, freshness, and user value after applicable P0 gates.
- `priority:P2` — scale, convenience, optional sources, and future capabilities after the production path is dependable.

Priority never overrides a dependency, source-access condition, review gate, or owner-input requirement.

Generic implementation, source-proposal, correction, and not-found issue forms deliberately start as `priority:P2` + `status:planned` so an untriaged public issue can never accidentally jump ahead of reviewed work or violate taxonomy. The continuous agent/maintainer must inspect impact and dependencies and may promote it to P1/P0 before marking it ready. The dedicated Catalog Incident form starts P0 because it is reserved for serious current correctness/safety failures.

## Status lifecycle

- `status:planned` — scoped but not yet proven ready.
- `status:ready` — dependencies and required owner input are satisfied.
- `status:in-progress` — the one mutable implementation issue currently being worked.
- `status:review` — implementation is finalized; CI/review/merge/post-merge work remains.
- `status:blocked` — blocked on another repository prerequisite or failure.
- `status:blocked-external` — blocked on a third-party source, permission, API, contract, or other external condition.
- `status:needs-owner-input` — blocked on a concrete action from the repository owner.
- `status:done` — merged and post-merge validation completed.

At most one issue may carry `status:in-progress`.

## Selecting work

At the start of an autonomous invocation:

1. Read `AGENTS.md`, current canonical feature specifications, and the master epic.
2. Continue the existing `status:in-progress` issue if one exists and is still valid.
3. Otherwise list open `status:ready` issues.
4. Choose P0 before P1 before P2.
5. Within the same priority, prefer the lowest-numbered ready issue unless a dependency graph in the epic specifies another order.
6. Change only that issue to `status:in-progress` before creating its branch.
7. If it becomes blocked, record the blocker and transition it to the appropriate blocked status before selecting another ready issue.

Research may inspect future issues, but substantial implementation belongs to one mutable issue at a time.

## Required issue sections

Implementation issues should make the following durable and explicit:

- **Goal** — bounded outcome.
- **Canonical specs** — feature-spec requirements that govern behavior.
- **Dependencies** — issue references and external prerequisites.
- **Checkpoints** — ordered progress checkboxes.
- **Tests / validation** — unit, integration, workflow, iOS, data-quality, security, or performance evidence as applicable.
- **Owner input** — only when a concrete action is required; never include credential values.
- **Release/catalog impact** — schema, source, app, catalog, or operational implications.

Use dependency checkboxes such as:

```markdown
## Dependencies
- [x] #1 — foundation merged and verified
- [ ] #7 — reusable workflow contract must land first
```

Owner-input sections must name the action and the expected non-secret outcome. They must never ask for passwords, tokens, private keys, private contracts, personal addresses, or other secret values in a public issue.

## Pull requests and completion

A pull request is opened only when the issue branch is finalized, tested, reviewed locally, and considered merge-ready. A PR is not a workspace for unfinished implementation.

After merge, review the exact integrated `main` commit and required CI. Only then transition the issue to `status:done` and close it.

## Managed labels

`.github/labels.json` is the source of truth for managed priority, status, type, area, and owner-action labels. `Tools/github_governance.py` validates the manifest, synchronizes managed labels, and checks that repository issues have exactly one priority and one status label. Synchronization updates/creates managed labels but intentionally does not delete unrelated GitHub labels.
