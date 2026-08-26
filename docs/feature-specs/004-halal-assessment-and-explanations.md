# 004 — Halal assessment and explanations

**Status:** Accepted  
**Last reviewed:** 2026-08-26

## Principle

The app reports evidence, not confidence theatre. It must never collapse ambiguous or missing information into a positive result merely to give a binary answer.

## Assessment statuses

1. **`halal-certified`** — a current, recognized certification applies to the exact product/formulation/market and no stronger contradictory evidence is present.
2. **`halal-reviewed`** — a dated human or approved deterministic review found sufficient evidence for the defined methodology, but no current recognized certification is claimed.
3. **`not-halal`** — reliable evidence identifies a prohibited ingredient/source, explicit manufacturer declaration, or other disqualifying fact under the methodology.
4. **`questionable`** — evidence exists but an ingredient origin, processing aid, flavour carrier, alcohol use, certification scope, market formulation, or source conflict prevents a responsible positive/negative conclusion.
5. **`unknown`** — ingredients, provenance, review, or other minimum evidence is absent or unusable.

- **HF-ASSESS-001:** The stored status must be one of the five values above.
- **HF-ASSESS-002:** `halal-certified` must name the certifying body and certificate/reference, scope, and validity/review date when available.
- **HF-ASSESS-003:** `halal-reviewed` must identify the methodology version and review date and must not display a certification badge.
- **HF-ASSESS-004:** Ingredient absence is not proof of absence. Empty, truncated, unreadable, machine-translated-only, or unlicensed ingredient text produces `unknown` unless stronger independent evidence exists.
- **HF-ASSESS-005:** Source-dependent ingredients such as gelatine, enzymes, rennet, mono- and diglycerides, glycerol, flavourings, emulsifiers, processing aids, and alcohol carriers produce `questionable` unless their relevant origin/process is evidenced.
- **HF-ASSESS-006:** Explicit porcine material or another methodology-defined prohibited substance produces `not-halal` when the source observation is reliable and current enough.
- **HF-ASSESS-007:** Conflicting credible sources must be displayed and must not be silently resolved in favor of a positive result.
- **HF-ASSESS-008:** Each assessment must have at least one structured reason except `unknown` caused solely by no product evidence, which still requires a standard missing-evidence reason.
- **HF-ASSESS-009:** Every reason contains a stable code, user-facing title, explanation, optional ingredient/evidence reference, and evidence severity.
- **HF-ASSESS-010:** The UI must distinguish assessment status from evidence freshness. A stale `halal-reviewed` record remains historically reviewed but carries a prominent stale warning.
- **HF-ASSESS-011:** A formulation change invalidates reuse of the prior assessment until the new observation is reviewed.
- **HF-ASSESS-012:** The methodology must be documented and versioned before real products are classified in bulk.

## Precedence

For the same observation, reliable explicit prohibited evidence overrides positive ingredient heuristics. A valid exact-scope certification may support `halal-certified`, but unresolved evidence that the certificate applies to another market/formulation lowers the result to `questionable`. `unknown` is used when there is insufficient evidence; it is not a weaker synonym for `questionable`.

## Explanation layout

A product result must show, in this order:

1. status label and symbol;
2. one-sentence summary;
3. freshness warning, if any;
4. ordered reasons/evidence;
5. exact ingredient text and language;
6. source and source record reference;
7. observed/retrieved/reviewed dates;
8. methodology and catalog versions; and
9. informational disclaimer.

Color may reinforce status but may never be the only status signal.
