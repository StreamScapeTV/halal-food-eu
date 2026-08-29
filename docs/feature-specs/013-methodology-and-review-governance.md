# 013 — Methodology and review governance

**Status:** Accepted  
**Last reviewed:** 2026-08-26

## Purpose

The application can only be trustworthy when its assessment method is explicit, versioned, reviewable, and honest about religious interpretation. This specification governs how real-product conclusions may enter a released catalog; the current bundled records remain synthetic demonstrations.

## Methodology authority

- **HF-METHOD-001:** Before any bulk real-product classification, the repository must contain a human-readable methodology document whose version exactly matches `methodologyVersion` in the catalog.
- **HF-METHOD-002:** The methodology must define prohibited evidence, source-dependent ingredients, certification acceptance criteria, market/formulation matching, conflict handling, freshness thresholds, and the boundary between `questionable` and `unknown`.
- **HF-METHOD-003:** Religious conclusions and interpretation profiles require review by appropriately qualified human advisers. Software contributors and automated systems must not present themselves as scholars or certification bodies.
- **HF-METHOD-004:** The default methodology must not claim universal agreement across schools of jurisprudence. Material interpretive differences must be represented explicitly rather than hidden in one status.
- **HF-METHOD-005:** A methodology change that can alter user-visible meaning requires a new methodology version, impact analysis, catalog re-evaluation, sampled human review, and release notes.
- **HF-METHOD-006:** Historical assessments retain the methodology version under which they were made; changing current rules must not rewrite audit history.

## Automation boundary

- **HF-AUTO-001:** Importers, parsers, OCR, language models, ingredient taxonomies, and deterministic rules may propose structured evidence and candidate outcomes, but their output is untrusted until the release policy accepts it.
- **HF-AUTO-002:** Automation must preserve original source text and expose the rule/model/tool version, confidence or ambiguity, and transformation steps.
- **HF-AUTO-003:** An automated absence check must never produce `halal-certified` and must not produce `halal-reviewed` unless an accepted methodology explicitly permits the exact deterministic case and release review validates it.
- **HF-AUTO-004:** Any explicit porcine/prohibited match proposed by automation must retain the exact ingredient/source evidence and be reviewable for negation, translation, context, and false positives.
- **HF-AUTO-005:** An ambiguous additive or flavour must remain `questionable` unless reliable origin/process evidence resolves it. Automation may not guess source from statistical likelihood.
- **HF-AUTO-006:** Machine-generated prose must not invent evidence, certificate identifiers, manufacturer statements, dates, or source references.

## Review workflow

- **HF-REVIEW-001:** Every real-product observation has a review state separate from the consumer assessment status: `unreviewed`, `in-review`, `approved`, `rejected`, or `superseded`.
- **HF-REVIEW-002:** A production catalog contains only observations and assessments that satisfy the release policy for their source and risk category.
- **HF-REVIEW-003:** High-risk positive outcomes, certification claims, status changes from negative/questionable to positive, source conflicts, and methodology migrations require independent second review.
- **HF-REVIEW-004:** A reviewer must see original ingredients, language, source reference, observed/retrieved dates, prior observation diff, candidate reasons, and applicable methodology before approval.
- **HF-REVIEW-005:** Reviewer identity may be pseudonymous publicly, but the project must retain a durable audit identifier and timestamp without publishing unnecessary personal data.
- **HF-REVIEW-006:** Rejection and supersession preserve the record and reason; they do not erase history.
- **HF-REVIEW-007:** Corrections with consumer-safety impact must be prioritized, documented, and shipped through the normal validated catalog release process.

## Interpretation profiles

A future app version may offer named interpretation profiles only after qualified review defines their exact differences.

- **HF-PROFILE-001:** The application must always show which methodology/profile produced the displayed outcome.
- **HF-PROFILE-002:** A profile changes assessment interpretation, not source facts. Original ingredients and provenance remain identical.
- **HF-PROFILE-003:** Where profiles disagree, the UI must expose that disagreement and must not choose a more reassuring result silently.
- **HF-PROFILE-004:** The app must provide a neutral default that emphasizes evidence and uncertainty until profile governance is accepted.

## Audit and release evidence

Each production catalog release must retain:

1. methodology document/version;
2. logical source-input snapshot or reproducible references;
3. transformation/tool versions;
4. observation and assessment diffs;
5. required review approvals;
6. validator output and manifest digest; and
7. a record of known limitations, conflicts, and stale evidence.

This audit material may live outside the distributed app when source permissions or privacy require it, but the released database must retain enough identifiers to trace every displayed conclusion.