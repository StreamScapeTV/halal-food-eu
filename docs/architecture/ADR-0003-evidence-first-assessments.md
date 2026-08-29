# ADR-0003 — Evidence-first assessment states

**Status:** Accepted  
**Date:** 2026-08-26

## Context

Ingredients alone often cannot establish animal source, processing aids, flavour carriers, certification scope, or market-specific formulation. A forced binary answer would create false certainty.

## Decision

Use five explicit states: `halal-certified`, `halal-reviewed`, `not-halal`, `questionable`, and `unknown`. Store ordered structured reasons, source provenance, dates, and methodology version. Treat freshness separately from status. A changed ingredient observation requires re-review.

## Consequences

The UI can still answer quickly, but it must expose uncertainty. Positive records are more expensive to curate because they require sufficient evidence. This is intentional: product trust is more important than maximizing positive classifications.
