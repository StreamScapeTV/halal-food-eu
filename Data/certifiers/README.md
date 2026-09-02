# Certifier registry

This directory contains the reviewed admission registry used to decide whether halal certification evidence may support a `halal-certified` assessment.

The production registry starts with **no accepted real certifier**. Adding a real accepted certifier/scheme is a qualified human-review decision under `docs/feature-specs/013-methodology-and-review-governance.md` and `023-certifier-registry-and-certificate-validity.md`; it is not a web-discovery or parser decision.

Files:

- `certifier-registry-v1.json` — canonical production admission state.
- `certifier-registry-v1.schema.json` — strict machine-readable registry contract.

Important boundaries:

- registry admission does not authorize source extraction or redistribution;
- automated certificate sources require a separately reviewed source-policy/credentials contract;
- a registered certifier does not make every certificate, logo, brand, facility, or product claim valid;
- exact product, market, current formulation, certificate state, review window, and recheck age remain independently validated;
- the app performs no runtime certificate-network lookup.
