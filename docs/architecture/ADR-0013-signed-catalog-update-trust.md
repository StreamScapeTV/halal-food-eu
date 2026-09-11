# ADR-0013 — Bundled Ed25519 trust roots for optional signed catalog delivery

**Status:** Accepted  
**Date:** 2026-09-09

## Context

Specification 029 and ADR-0012 permit optional post-install market SQLite modules while retaining the complete bundled Germany catalog. Issue #25 must make remote delivery useful without turning GitHub/HTTPS, a mutable tag, a release account, or an Actions secret into a runtime trust authority.

The downloaded bytes are public and hostile until verified. A signing-key compromise also needs an explicit recovery path: an attacker who only controls GitHub hosting must not be able to invent trusted bytes, and a private signing secret must not be able to introduce a new public key to an already-shipped app.

## Decision

Use **Ed25519** through Apple CryptoKit (`Curve25519.Signing`) in the iOS runtime and OpenSSL Ed25519 in repository release tooling. The signed object is the exact canonical UTF-8 JSON bytes of `catalog-module-manifest.json` using sorted object keys, compact separators, unescaped slashes, and one trailing newline. The detached signature is exactly 64 bytes.

### Trust root and key states

The app bundles `Data/catalog/catalog-module-trust-policy-v1.json`. It is a closed, bounded public policy containing key IDs, raw 32-byte public keys encoded as base64, states (`active`, `retired`, `revoked`), revoked module IDs, and revoked database SHA-256 values.

- `active`: may authorize a new download and verify an installed module.
- `retired`: may verify an already-installed module during a planned overlap/rotation period, but cannot authorize a new download.
- `revoked`: cannot verify new or persisted modules.

A persisted module is fully re-verified against the current bundled policy before it is registered with the market router. Installation history is never a trust cache.

The policy may intentionally ship with no active key. That is the initial safe state: all bundled offline behavior works, update discovery reports unavailable, and production signing fails closed. A deterministic RFC 8032 key is used only in tests and is explicitly forbidden from the shipping policy.

### Private signing material

Production signing uses only the GitHub Environment secret `CATALOG_SIGNING_PRIVATE_KEY`, a base64-encoded 32-byte Ed25519 private seed. The secret is exposed only as environment state to the manual protected-main signing step. The release tool derives its public key and refuses to sign unless it exactly matches the requested `active` public key already committed/bundled in the reviewed trust policy.

This means adding a GitHub secret, changing a repository variable, or controlling release hosting cannot add a runtime trust root. Trust-root changes require a reviewed app/source change.

### Release identity and publication

A module is identified by `(market, catalogVersion)`:

- module ID: `<ISO2>-<catalogVersion>`;
- GitHub release/tag: `catalog-<iso2-lower>-<catalogVersion>`.

These identities are immutable and cannot be reused for different bytes. The manifest also binds exact database/inner-manifest/attribution digests, market, runtime/methodology/app versions, counts, coverage limitations, source/release identity, size measurements, publication time, signing key ID, and optional superseded module ID.

Only successful protected-`main` `catalog-release.yml` **production** evidence for the same exact `main` SHA can enter the signing workflow. The workflow revalidates production SQLite and redistribution lineage, signs locally, verifies locally, refuses an existing tag/release, creates a prerelease, re-downloads all fixed assets through their public HTTPS URLs without using those URLs as trust anchors, verifies them again independently, attaches a release report, and only then marks the release stable.

If verification fails after this workflow created the prerelease, it cleans only that exact run's prerelease/tag. A release that was already stable is never silently rewritten.

### Runtime download and activation

The app uses an ephemeral URL session and only the reviewed GitHub Releases metadata/asset hosts. Requests contain no scan, search, saved-item, submission, account, device, analytics, or location identifiers. Product/catalog text never selects a URL.

Candidates are downloaded with fixed names and byte/time/redirect/host bounds into isolated temporary storage. Files/directories must be regular non-symlink paths. Before activation the app verifies canonical manifest bytes, trust state, signature, every digest/byte count, exact market, inner production manifest identity/counts, SQLite integrity/counts, and the actual production lookup/search repositories. Only then are bytes atomically promoted to a versioned Application Support directory excluded from backup.

The previous verified version remains available. `DE` always has the immutable bundled catalog as the final fallback. A different market with no verified module is explicitly unavailable and never falls through to Germany.

## Rotation and incident response

A normal rotation is staged across app releases:

1. ship the new public key in the bundled policy before its private key is used;
2. optionally overlap old/new active keys while adoption occurs;
3. start signing new modules with the new key only after that app trust root exists;
4. retire the old key to prevent new downloads while allowing installed known-good modules during the transition;
5. revoke the old key/module/database in a later reviewed app policy when continued acceptance is unsafe.

For a compromised signing key or malicious catalog, stop publication, keep/bury the bad release from normal discovery, publish user-facing incident information, identify affected module/app versions, and ship a reviewed trust-policy revocation. Deleting a public release is not treated as proof that mirrored bytes disappeared; cryptographic rejection is the authority.

A remotely mutable trust-root/channel-key service is rejected for the initial implementation. It would add another online trust protocol and operational key hierarchy without being required for offline catalog updates. A later accepted ADR may add a separately rooted signed revocation channel if incident-response measurements justify it.

## Alternatives rejected

### HTTPS or GitHub provenance alone

Rejected. Hosting/account compromise or mutable release metadata would become a catalog trust decision. GitHub attestation is supplemental evidence only.

### Public key delivered beside the module

Rejected. It lets the download choose the key that verifies itself.

### Symmetric HMAC shared with the app

Rejected. A verifier secret embedded in the app is extractable and would also be a signing credential.

### Automatically trusting an installed module forever

Rejected. It prevents a later app trust-policy revocation from taking effect.

### Network-first lookup/update service

Rejected. It conflicts with the offline/privacy product contract and is unnecessary for immutable SQLite modules.

## Consequences

- The app has no third-party runtime dependency; CryptoKit supplies signature verification.
- Production update availability is deliberately off until an owner provisions a real key pair by committing the public key in a reviewed app release and separately configuring the matching private seed in the protected GitHub environment.
- CI remains fully secretless using deterministic fixtures.
- Key compromise requiring immediate revocation may still require an app update in this initial design; the bundled Germany fallback and optional/manual update behavior bound the failure while a later separately rooted revocation channel remains possible.
