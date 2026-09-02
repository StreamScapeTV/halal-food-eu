# 023 — Certifier registry and certificate validity

**Status:** Accepted  
**Last reviewed:** 2026-09-02

## Purpose

Halal certification is independent evidence. A logo, brand claim, facility certificate, or the existence of a certificate row is not sufficient to produce `halal-certified`. This specification makes certifier/scheme admission and current certificate eligibility explicit, versioned, and fail-closed.

The initial production registry contains no accepted real certifier. Adding a real accepted entry is a qualified human-review decision and must not be performed automatically from public-web discovery.

## Canonical registry

`Data/certifiers/certifier-registry-v1.json` is the canonical certifier/scheme admission registry. `Data/certifiers/certifier-registry-v1.schema.json` is its machine-readable shape and `Tools/certifier_registry.py` is the strict validator/eligibility implementation.

- **HF-CERT-001:** Registry identity and policy version are immutable release inputs. Unknown fields, duplicate certifier/scheme keys, malformed dates, unknown states, or malformed source approvals fail closed.
- **HF-CERT-002:** Registry states are `accepted`, `review-required`, `blocked`, or `revoked`. Only an `accepted` entry within its review window can support a certified product.
- **HF-CERT-003:** Every accepted entry records stable certifier/scheme keys, exact evidence identifiers, legal/display names, markets, official reference, reviewed/next-review dates, durable reviewer ID, limitations, allowed app wording, allowed exact-match kinds, maximum certificate recheck age, and one or more approved source declarations.
- **HF-CERT-004:** Every automated source declaration names its separate source-policy approval reference and credential-variable contract. A registry entry does not itself authorize extraction or redistribution.
- **HF-CERT-005:** Real certifier admission requires appropriately qualified human review under specification 013. Software automation may validate an admitted record but may not decide that a new certifier is religiously acceptable.

## Exact certificate eligibility

Certification records remain immutable evidence under specification 014. The registry does not rewrite them.

- **HF-CERT-006:** `halal-certified` requires an immutable certificate ID whose `certifier`, `scheme`, `sourceKey`, GTIN, and market exactly match an accepted registry entry and the product under review.
- **HF-CERT-007:** Automatic certified eligibility accepts only explicit exact match kinds: `exact-gtin`, `explicit-product-list`, or `exact-batch`. `name-only`, `brand-only`, `facility-only`, `logo-only`, missing, free-form, or unknown match kinds are review-only and cannot create `halal-certified`.
- **HF-CERT-008:** A certificate supporting `halal-certified` must bind `ingredientObservationID` to the exact current formulation. Certification does not refresh or replace ingredient evidence.
- **HF-CERT-009:** A certificate supporting `halal-certified` must declare `status=active`. Not-yet-effective, expired, revoked, suspended, `unknown`, or stale-recheck evidence cannot support a current certified assessment.
- **HF-CERT-010:** `lastCheckedAt` ages independently from formulation evidence. The accepted registry entry defines the maximum allowed recheck age for its scheme/source.
- **HF-CERT-011:** Facility, brand, logo, and name-only evidence may remain stored for review/audit when lawful, but it must not be projected to unrelated products or used as certified status evidence.
- **HF-CERT-012:** Certificate limitations are preserved separately from scope and are displayable offline. Limitations never broaden certified scope.

## Invalidation and review

- **HF-CERT-013:** A current `halal-certified` assessment is invalidated when none of its linked certificates remains eligible under the current registry and selected formulation.
- **HF-CERT-014:** Deterministic invalidation reasons include registry state/review expiry, source disallowance, GTIN/market/formulation mismatch, non-exact scope, not-yet-effective/expired/revoked/suspended/unknown status, and stale recheck.
- **HF-CERT-015:** Registry/certificate invalidation emits normal immutable assessment validity events. Historical certification and assessment rows are retained.
- **HF-CERT-016:** Certification additions, reinstatement, or a transition to a positive status still require the independent review required by HF-REVIEW-003; deterministic validation never grants a positive religious conclusion by itself.
- **HF-CERT-017:** Current formulation conflicts continue to block positive status even when certificate evidence is otherwise eligible.

## Runtime presentation

The iOS app remains offline-first and uses the bundled catalog. It performs no certificate-network lookup during product viewing.

- **HF-CERT-018:** For linked certification evidence the result UI can show certifier, scheme, certificate reference, scope, effective/expiry dates, last-checked date, source provenance, and limitations.
- **HF-CERT-019:** Certification presentation is subordinate to the current assessment validity state and must not imply nationwide availability, universal scholarly agreement, or broader certificate scope.

## Source and credentials boundary

- **HF-CERT-020:** Public visibility is not source permission. Any future automated certifier adapter requires its own admitted source policy/rights review and source-prefixed public configuration/secret contract before acquisition.
- **HF-CERT-021:** The initial registry/validator path requires no new account, API key, paid source, or runtime network dependency.

## Acceptance tests

Tests must cover accepted exact GTIN/formulation scope; similar-name, brand, facility and logo rejection; market/formulation mismatch; effective/expiry/revocation/suspension/unknown status; stale recheck; registry blocked/revoked/review-expired state; source disallowance; deterministic invalidation events; no hidden positive inference; and offline presentation of certificate limitations.
