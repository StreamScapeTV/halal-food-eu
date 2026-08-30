# ADR-0009: Versioned halal methodology and explicit human review

- Status: Accepted
- Date: 2026-08-30
- Scope: issue #11

## Decision

Halal Food EU treats ingredient parsing as **candidate detection**, not as a verdict engine. The versioned methodology in `Data/methodology/halal-methodology-v1.json` defines multilingual aliases, evidence categories, candidate outcomes, context exclusions, review queues, authority references, and certification-policy boundaries. The parser may emit only `unknown` or `questionable`; it cannot emit `halal-certified`, `halal-reviewed`, or final `not-halal` merely from text matching or absence of matches.

An assessment becomes authoritative only through immutable evidence plus an explicit review record. Every review is bound to the exact GTIN, market, ingredient observation/content hash, methodology version, reviewer, review timestamp, next-review timestamp, limitations, reason, and cited evidence IDs. The review exchange is described by `halal-review-input-v1.schema.json`, and the resulting durable review artifact by `halal-review-artifact-v1.schema.json`.

## Status semantics

- `unknown` means evidence is absent or insufficient for a conclusion. No candidate match is still `unknown`, never a positive result.
- `questionable` means material ambiguity, conflict, OCR/transformation uncertainty, or other caution remains.
- `not-halal` requires a reviewer to confirm positive prohibited evidence in the exact current ingredient observation. A keyword candidate alone is insufficient.
- `halal-reviewed` requires a completed explicit human review under the named methodology. It is a review conclusion and must never be presented as certification.
- `halal-certified` requires exact current product/market certification evidence from a certifier and scheme explicitly admitted by the versioned certification policy. The initial policy admits no certifier by default, so certification fails closed until reviewed certifier data is added.

## Evidence and context rules

Matching preserves exact source-text spans so reviewers can see what was actually present rather than a normalized paraphrase. German and English aliases are language-scoped; universal E-number aliases are identity hints only. Context-sensitive exclusions are permitted where an alias would otherwise be misleading, such as distinguishing alcohol/ethanol review candidates from named sugar alcohol contexts.

OCR-only, machine-assisted, transformed, stale, date-unknown, changed, or conflicting formulation evidence is routed to explicit review queues. A positive review requires fresh exact formulation evidence, all open queues explicitly resolved with evidence, and no unresolved current formulation conflict.

## Additive identities

`Data/methodology/additive-identities-v1.json` is an identity-only reference set. It may map an E-number to multilingual names and public reference sources, but its schema requires `originConclusion: unknown-without-evidence` and `halalConclusion: null`. Identity data therefore cannot silently become an ingredient-origin or halal classification source. Broader EU/EFSA additive coverage remains issue #29.

## Formulation and methodology changes

Assessments are immutable history. A changed selected ingredient observation, changed methodology version, changed selected certification set for a certified result, or newly represented formulation conflict produces a migration decision to invalidate rather than rewrite the old assessment. The migration tool can emit canonical validity events while preserving the original review and assessment records.

Only records affected by incompatibility need reassessment; compatible historical evidence remains available. The methodology changelog and stable rule/reason identifiers provide the basis for future targeted migrations.

## Workflow boundary

`halal-methodology.yml` is an additive read-only reusable workflow. It consumes already validated normalized evidence and the #10 quality report, then emits deterministic candidate/review-queue and migration reports. It neither mutates repository state nor assigns final halal decisions.

Issue #12 owns integration of reviewed assessment changes and methodology migration output into the final production SQLite/proposal/rollback path. This keeps #11 from manufacturing human decisions in automation while giving #12 a deterministic, reviewable input contract.

## Consequences

- No-match, missing data, and ambiguous origin remain truthful rather than becoming false reassurance.
- Human decisions are reproducible because the evidence, methodology version, source span, reviewer, limitations, and next-review date are preserved.
- Certification is structurally distinct from ingredient review.
- Retailer observations never become halal evidence merely because they are present in the same product record.
- Methodology changes can invalidate current applicability without erasing history.
- Candidate rules can expand independently of the iOS runtime while the app continues consuming the existing immutable assessment model.
