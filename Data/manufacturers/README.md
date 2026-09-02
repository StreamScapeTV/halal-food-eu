# Manufacturer and producer-origin evidence

This directory defines workflow-side manufacturer provenance artifacts for Halal Food EU.

The initial cohort is **not** a direct manufacturer feed. It preserves field-level producer provenance already published through the admitted Open Food Facts source. Open Food Facts remains the source/license/attribution boundary.

Generated normalized artifacts may include:

- `producer-provenance.json` — immutable confirmed exact-field producer provenance keyed to canonical ingredient evidence IDs;
- `manufacturer-target-queue.json` — deterministic review/prioritization items for confirmed producer formulations, provenance candidates/ambiguities, missing ingredients and formulation changes.

The acquisition snapshot never retains arbitrary raw `owner_fields` or producer/PIM payloads. Reviewed owner fields are reduced to SHA-256 hashes before storage, and manufacturer source records are restricted to a bounded metadata allowlist.

These sidecars do not change the v1 evidence envelope, runtime SQLite catalog, ingredient `verificationState`, freshness, retailer evidence, certification evidence, or halal assessment. Direct manufacturer APIs/feeds remain separate future sources requiring explicit collection and redistribution rights.

See `docs/feature-specs/022-manufacturer-provenance.md` for the accepted contract.
