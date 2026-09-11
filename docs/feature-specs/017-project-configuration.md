# 017 — Public project configuration and optional source credentials

**Status:** Accepted  
**Last reviewed:** 2026-09-09

## Purpose

Halal Food EU keeps public project identity in source control and introduces external credentials only for a separately approved source that actually needs them. Public contact data is configuration, not a secret. Secret **values** never enter Git, issue bodies, workflow artifacts, logs, app resources, or catalog data.

The canonical v1 public configuration is `Data/config/public-project-configuration-v1.json`. The accepted values are:

- `PRODUCT_SUBMISSION_EMAIL=info@faruqi.dev`
- `OPEN_FOOD_FACTS_CONTACT_EMAIL=info@faruqi.dev`
- `OPEN_FOOD_FACTS_USER_AGENT=HalalFoodEU/0.1 (info@faruqi.dev)`

## Public configuration

- **HF-CONFIG-001:** The three public values above are committed once in the canonical configuration and validated in CI. Workflows and app features consume that source of truth instead of depending on manually created repository variables.
- **HF-CONFIG-002:** The Open Food Facts User-Agent must identify `HalalFoodEU/<major>.<minor>` and contain exactly the configured public Open Food Facts contact email.
- **HF-CONFIG-003:** `PRODUCT_SUBMISSION_EMAIL` is the public recipient consumed by the backend-free product-submission flow in specification 018. Its presence does not introduce an account, project backend, secret, or mandatory network dependency; the app bundles this public configuration for the user-directed composer.
- **HF-CONFIG-004:** A public value may be overridden later only for a documented operational reason. The checked-in configuration remains the default and must stay valid independently of repository/environment variables.

## Optional credential contracts

- **HF-CONFIG-005:** The initial approved production sources require zero third-party credentials. `GITHUB_TOKEN` is GitHub-provided automation identity and must never be created or redefined as a source secret.
- **HF-CONFIG-006:** A credential-bearing source is permitted only after the source is registered and approved by the catalog source contract. The exact credential **names** and one selected authentication mode live in `Data/sources/<sourceKey>/credential-policy-v1.json`, owned by that source's review. A central configuration file must not invent speculative source secrets.
- **HF-CONFIG-007:** Authentication modes are mutually exclusive per source contract. The v1 contract selects exactly one mode and one exact unique set of required secret names; secret names are metadata and secret values are never source-controlled.
- **HF-CONFIG-008:** Disabled optional sources never block ordinary CI or free-source acquisition merely because their credentials are absent. An enabled source marked `credentialsRequired` fails closed unless it has a reviewed source-specific credential policy and all exact required credential names are reported configured.
- **HF-CONFIG-009:** Trusted workflows may report configured/not-configured state using explicit booleans or credential names only. They must never serialize the GitHub `secrets` context, dump the environment, dynamically select secret names, or place credential values in arguments, artifacts, summaries, or issues.

## Configuration health

- **HF-CONFIG-010:** Pull-request configuration validation runs with `contents: read`, uses no third-party secret, and validates the public values, source registry relationship, credential-policy shape, and fail-closed health behavior.
- **HF-CONFIG-011:** `configuration-health.yml` runs only from reviewed `main` code on schedule/manual dispatch. It has only `contents: read` plus the `issues: write` needed to reconcile the bounded owner-input incident.
- **HF-CONFIG-012:** The health report is metadata-only. For credential-bearing sources it may contain source key, selected authentication mode, required secret names, and a configured boolean. It must not contain or derive secret values.
- **HF-CONFIG-013:** Missing required credentials for an enabled approved source create or update one deduplicated `[Configuration Health] Owner input required` issue. Repeated checks update the same logical issue; a later healthy check closes it. The issue tells the owner which credential names are missing and explicitly forbids pasting values into GitHub.
- **HF-CONFIG-014:** A future source that needs credentials must update its source-specific credential contract and the trusted health workflow with explicit presence-only bindings in the same reviewed change. Until that binding exists, health treats the enabled credential set as missing rather than guessing or reading broad secret state.

## Runtime boundaries

- **HF-CONFIG-015:** Open Food Facts bulk acquisition reads its User-Agent from the committed public configuration. No repository variable or third-party credential is required for the approved bulk path.
- **HF-CONFIG-016:** Public configuration may be packaged where a user-facing feature needs it, but API keys, OAuth secrets, SFTP passwords/keys, signing material, and private source tokens must never be embedded in the iOS app or SQLite catalog.
- **HF-CONFIG-017:** Configuration health and CI are repository automation only. They do not add a backend, user account, analytics, tracking, or mandatory runtime network access to the iOS product.
- **HF-CONFIG-018:** Specification-029 signing uses one explicit GitHub Environment secret name, `CATALOG_SIGNING_PRIVATE_KEY`, containing a base64-encoded 32-byte Ed25519 private seed. It is read only as environment state inside the manual protected-main release step; it is never passed as a command-line value, committed, emitted, artifacted, or embedded in the app/catalog.
- **HF-CONFIG-019:** The public verification authority is the bundled `Data/catalog/catalog-module-trust-policy-v1.json`. Key IDs/public keys/states and revoked module/database digests are public security configuration. A production signing key is usable only when its derived public key exactly matches one `active` entry already shipped by the app. Repository/environment variables cannot add a runtime trust root.
- **HF-CONFIG-020:** The committed trust policy may intentionally contain zero active keys. In that state the app keeps all bundled offline functionality but reports signed updates unavailable, and the release workflow fails before signing even if a private-key secret exists. Deterministic CI/test keys must never be copied into the production trust policy.
- **HF-CONFIG-021:** Key rotation is staged through reviewed app releases: introduce the new public key before using its private key, overlap trusted keys only when needed, retire an old key to allow persisted known-good modules without new downloads, and revoke a compromised key/module/database through a new reviewed bundled trust policy plus release/incident response. No hidden remote key-discovery mechanism is accepted.

## Acceptance tests

The repository tests cover exact public values, email/User-Agent binding, zero-secret free sources, disabled optional sources, enabled missing-credential failure, source-specific credential-policy requirements, mutually-exclusive authentication shape, `GITHUB_TOKEN` exclusion, unknown secret-state rejection, metadata-only health output, deduplicated issue creation/update/closure, PR secret isolation, trusted-main health isolation, Open Food Facts acquisition use of the committed User-Agent, specification 018 consumption of `PRODUCT_SUBMISSION_EMAIL` as public app configuration without embedding any secret, and specification-029 signing-key/trust-policy separation including fail-closed empty policy and deterministic-test-key exclusion.
