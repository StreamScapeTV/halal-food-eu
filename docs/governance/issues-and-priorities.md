# GitHub issue governance

GitHub Issues are the durable execution system for this standalone repository: they own backlog intent, specification, status history, discussion, and completion evidence. The repository does not require a repository Orchestrator or multi-agent scheduler for normal work.

Current StreamScapeTV organization rules may also require Agent State. Agent State is a bounded current operational layer for assignment, work/resources, issue readiness/dependencies, CI state, and cleanup; it is not a second backlog or durable history store.

## Priority

- `priority:P0` — correctness, safety, security, data integrity, core architecture, or release blockers. Select a ready P0 before lower priorities.
- `priority:P1` — material production coverage, freshness, and user value after applicable P0 gates.
- `priority:P2` — scale, convenience, optional sources, and future capabilities after the production path is dependable.

Priority never overrides a dependency, source-access condition, review gate, or owner-input requirement.

Generic implementation, source-proposal, correction, and not-found issue forms deliberately start as `priority:P2` + `status:planned` so an untriaged public issue can never accidentally jump ahead of reviewed work or violate taxonomy. The continuous agent/maintainer must inspect impact and dependencies and may promote it to P1/P0 before marking it ready. The dedicated Catalog Incident form starts P0 because it is reserved for serious current correctness/safety failures.

## Status lifecycle

GitHub status labels are the durable lifecycle record. Where shared organization rules require Agent State, keep the owned issue's bounded current operational state consistent with the durable transition; current operational state never replaces the GitHub history.

- `status:planned` — scoped but not yet proven ready.
- `status:ready` — dependencies and required owner input are satisfied.
- `status:in-progress` — the one mutable implementation issue currently being worked.
- `status:review` — implementation is finalized; CI/review/merge/post-merge work remains.
- `status:blocked` — blocked on another repository prerequisite or failure.
- `status:blocked-external` — blocked on a third-party source, permission, contract, API, or other external condition.
- `status:needs-owner-input` — blocked on a concrete action from the repository owner.
- `status:done` — merged and post-merge validation completed.

At most one issue may carry `status:in-progress`.

## Selecting work

At the start of an autonomous invocation:

1. Read `AGENTS.md`, current canonical feature specifications, the master epic, current GitHub issue facts, and the current Agent State issue/dependency state required by organization rules.
2. Continue an existing `status:in-progress` issue only when GitHub still shows it active and the current operational state shows the same owned issue without an unresolved blocker.
3. Otherwise consider only open GitHub `status:ready` issues whose current Agent State readiness/dependency state is also execution-ready.
4. Choose P0 before P1 before P2.
5. Within the same priority, prefer the lowest-numbered ready issue unless a dependency graph in the epic specifies another order.
6. Change only that issue to `status:in-progress`, register the bounded current operational ownership/resources required by organization rules, and then create its issue branch.
7. If GitHub and Agent State disagree, the current state is stale, or a blocker is unresolved, do not infer readiness from either side in isolation. Reconcile the owned current issue through the authorized lifecycle interface, or leave one bounded governance/cleanup obligation and select no conflicting work.
8. If active work becomes blocked, record the durable blocker and transition the issue to the appropriate GitHub blocked status; update bounded current operational state where authorized before selecting different ready work.

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

After merge, review the exact integrated `main` commit and required CI. Only then transition the GitHub issue to `status:done`, close it, and clear/release bounded current operational issue/work/resource state as required by organization rules.

## Managed labels

`.github/labels.json` is the source of truth for managed priority, status, type, area, and owner-action labels. `Tools/github_governance.py` validates the manifest, repository lifecycle authority, synchronizes managed labels, and checks that repository issues have exactly one priority and one status label. Synchronization updates/creates managed labels but intentionally does not delete unrelated GitHub labels.
