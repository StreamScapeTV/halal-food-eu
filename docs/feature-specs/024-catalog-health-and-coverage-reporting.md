# 024 — Catalog health and coverage reporting

**Status:** Accepted  
**Last reviewed:** 2026-09-02

## Purpose

Catalog health reports make evidence coverage, freshness, source state, review backlog, certification state, retailer evidence, and runtime/build health measurable without a standalone service. Reports describe the evidence corpus; they never turn partial public-source coverage into a complete retailer-assortment claim.

## Canonical artifacts

- **HF-HEALTH-001:** Every catalog proposal emits a machine-readable `catalog-health.json` and a human-readable `catalog-health.md` derived from the same exact evidence/quality inputs and repository revision.
- **HF-HEALTH-002:** Health output is deterministic for fixed inputs and includes a canonical SHA-256 digest. It contains aggregate/public metadata only; raw confidential payloads, credentials, personal submission data, and source-prohibited content are excluded.
- **HF-HEALTH-003:** Health reporting is a projection over accepted evidence/quality authorities. It may expose release blockers and incident identities but must not independently assign halal status, approve sources, admit certifiers, or alter evidence.

## Product and formulation coverage

- **HF-HEALTH-004:** Reports distinguish current product selections from current exact ingredient coverage. Missing ingredient evidence remains visible and cannot count as covered.
- **HF-HEALTH-005:** Reports expose current selection counts by market, brand, category, and source where those dimensions exist, plus ingredient language, conflicts, and the accepted formulation freshness buckets.
- **HF-HEALTH-006:** Change metrics separately expose additions, formulation changes, removals, and review-queue size. Record count alone is never presented as proof of useful coverage.

## Halal review and certification health

- **HF-HEALTH-007:** Current assessment counts are separated by `halal-certified`, `halal-reviewed`, `not-halal`, `questionable`, `unknown`, and `unassessed`. Parser candidates are not counted as reviewed assessments.
- **HF-HEALTH-008:** Reports preserve quality-gate blocker/warning codes, methodology-version coverage, and stable incident deduplication identities produced by the accepted quality gate.
- **HF-HEALTH-009:** Linked current certificate state and unmatched stored certificate count are separate metrics. Expired, revoked, suspended, not-yet-effective, stale/unknown-check states remain visible and never increase positive coverage.

## Retailer evidence and completeness

Allowed retailer claim states are:

- `no-evidence`
- `community-only`
- `observational-partial`
- `official-partial`
- `official-complete-snapshot`
- `degraded`

- **HF-HEALTH-010:** REWE, Lidl, and future retailer metrics remain separate. Official listing evidence, dated retailer observations, community evidence, and unknown/other evidence are counted independently.
- **HF-HEALTH-011:** `official-complete-snapshot` is allowed only from explicit official evidence that carries a reviewed complete-snapshot claim, a non-negative denominator, and successful denominator reconciliation. The presence of official product rows alone yields at most `official-partial`.
- **HF-HEALTH-012:** A previously strong official claim becomes `degraded` when its accepted freshness/health evidence is stale or otherwise degraded. A degraded report does not retain a completeness denominator as a current claim.
- **HF-HEALTH-013:** Human-readable reports repeat that retailer claim states describe the evidence corpus and do not imply nationwide/current stock unless the explicit complete-snapshot gate is satisfied.

## Source and runtime health

- **HF-HEALTH-014:** Source health exposes only bounded public metadata such as source key/class, retrieval time, current-product contribution, and available license/attribution indicators. Terms/source-rights approval remains owned by the source-policy/quality contracts.
- **HF-HEALTH-015:** Runtime/build metrics may include SQLite bytes, query latency, build duration, and manifest digest when measured evidence is available; absent measurements are represented as unavailable, not guessed.

## Workflow behavior

- **HF-HEALTH-016:** Catalog proposal quality artifacts include the health JSON/Markdown for the exact proposal inputs and expose the Markdown in the GitHub Actions job summary.
- **HF-HEALTH-017:** Scheduled/default-branch health checks validate the report/schema/contracts against trusted code and the committed runtime/evidence fixture even when no catalog proposal is open.
- **HF-HEALTH-018:** A blocking quality threshold still blocks the catalog proposal after health artifacts are written, so diagnostics remain available without weakening release gates.
- **HF-HEALTH-019:** Stable incident keys are suitable for deduplicated durable health issues. Issue mutation is permitted only from trusted default-branch workflows with bounded issue-write permission; ordinary pull requests/forks receive no such authority.

## Acceptance tests

Tests cover deterministic digesting, ingredient coverage versus product count, assessment separation, REWE/Lidl evidence separation, observational and official-partial claims, explicit denominator/reconciliation requirement for complete claims, degraded official evidence, certificate state/unmatched counts, absence of guessed runtime metrics, and human completeness wording.
