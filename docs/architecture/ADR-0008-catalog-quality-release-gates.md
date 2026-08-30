# ADR-0008: Catalog quality and release gates

- Status: Accepted
- Date: 2026-08-30
- Scope: issue #10

## Decision

Catalog publication is guarded by a deterministic, versioned quality policy in `Data/quality/catalog-quality-policy-v1.json` and a separately versioned source-terms review policy in `Data/quality/source-review-policy-v1.json`. Quality evaluation runs after normalized immutable evidence/change handoffs and before any catalog build that is admitted to publication.

The evaluator records one explicit proposal timestamp. Formulation, retailer, certification, and source-review clocks are evaluated against that timestamp so old evidence cannot become fresh merely because a workflow re-runs. Formulation defaults are refresh-recommended at nine calendar months and stale at twelve calendar months. Retailer and certification clocks remain independent. A missing formulation observation date is reported as `date-unknown`; retrieval time is provenance and does not silently become an observation date.

Stale or date-unknown formulation evidence is a visible warning when no unsafe positive conclusion survives. A formulation change, conflicting exact-market formulation, invalid certification, source-rights failure, excessive parser errors, or reviewed count regression can block publication. A positive assessment cannot be inherited across a different ingredient observation/hash. Real positive statuses require the configured independent review count; fixture evidence remains explicitly fixture-only.

## Source precedence

`sourceTrust` in the canonical policy is the deterministic evidence-tier rank used by later multi-source catalog compilation. It is not an automatic truth score. Before a rank can matter, evidence must be eligible for the exact GTIN, exact market/variant, applicable time, source rights, and evidence type. Package evidence and official manufacturer evidence therefore outrank open/community observations only after those exact-match constraints are satisfied.

Newer evidence does not automatically win when market or variant identity differs. Independent equivalent observations may increase reviewer confidence without erasing provenance. Materially conflicting active formulations remain represented as conflicts and must not be hidden by rank. Issue #12 may consume these ranks during multi-source SQLite compilation, but it must preserve these constraints rather than treating the numeric rank as permission to override conflicts.

## Reports and review

Each proposal produces a machine-readable JSON report and human-readable Markdown summary. Reports include product/current-ingredient coverage; formulation, retailer, and certification freshness; identity confidence; language, capture, verification, transformation, and source-revision coverage; assessment methodology/review state; rights and terms-review state; parser/count regressions; bounded change samples; deterministic source/category/status/new/changed samples; and mandatory high-risk review candidates.

All explicit positive statuses, certifications, represented conflicts, and changed records are included in the mandatory-review candidate set. Sampling is deterministic and widens when the configured sampled defect threshold is exceeded. Report artifacts are emitted before gate enforcement so a blocked proposal retains diagnostics.

## Incident behavior

Release blockers receive stable `catalog-health-*` deduplication identities derived from blocker condition and source. Severe semantic blockers mark quarantine and, where applicable, rollback as required. The quality stage itself does not mutate previously published history; downstream proposal/release/health automation uses these immutable findings to create correction work while preserving evidence history.

## Consequences

- Quality thresholds and source review lifecycle are reviewable source-controlled inputs rather than hidden workflow constants.
- Unknown/stale data remains truthful instead of being converted into a false positive or silently discarded.
- Source freshness, retailer availability, certification validity, and legal/source-review validity cannot refresh one another.
- Publication fails closed before SQLite generation when the quality report is blocked.
- Future source adapters and the multi-source builder must emit enough traceability/change data for this gate; they may not bypass it with source-specific shortcuts.
