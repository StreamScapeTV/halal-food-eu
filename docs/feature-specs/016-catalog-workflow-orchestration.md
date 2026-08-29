# 016 — Trusted catalog workflow orchestration

**Status:** Accepted  
**Last reviewed:** 2026-08-29

## Purpose

The production catalog pipeline is implemented as a set of small, reviewable GitHub Actions entrypoints backed by standard-library repository tooling. The workflows coordinate immutable artifacts; they do not collapse acquisition, normalization, assessment, SQLite compilation, app validation, and publication into one privileged script.

The canonical v1 workflow artifacts are:

- `Data/workflows/catalog-workflow-contract-v1.json` — stage, artifact, source, retry, retention, and generated-update policy;
- `Data/workflows/workflow-handoff-v1.schema.json` — machine-readable handoff envelope;
- `Data/workflows/sample-workflow-handoff-v1.json` and `synthetic-source-records.jsonl` — no-secret fixture contract;
- `Tools/catalog_workflow.py` plus focused `catalog_workflow_*` modules — standard-library semantic validation and deterministic key generation; and
- `.github/workflows/source-policy.yml`, `acquire-catalog.yml`, `scheduled-catalog-refresh.yml`, `normalize-and-diff.yml`, `catalog-quality.yml`, `build-catalog.yml`, `propose-catalog-update.yml`, `catalog-release.yml`, and `catalog-health.yml` — the bounded workflow surface.

Source-specific adapters extend the admitted source registry and repository tooling in their own issues. This specification does not approve a production source by naming a future adapter.

## Stage separation

- **HF-WORKFLOW-001:** The trusted pipeline preserves the ordered logical stages source policy → acquire → normalize/diff → quality → build → proposal → iOS validation → release, with health reporting orthogonal to release. A stage consumes explicit artifacts rather than hidden mutable runner state.
- **HF-WORKFLOW-002:** Acquisition and build are separate. `build-catalog` consumes previously admitted local artifacts and must never implicitly refresh a remote source.
- **HF-WORKFLOW-003:** Source-specific raw schemas are not the cross-stage API. Normalization produces the accepted immutable evidence contract from specification 014, and source selection consumes the contract from specification 015.
- **HF-WORKFLOW-004:** Production catalog material cannot become an accepted release merely because a scheduled acquisition succeeded. Policy, quality, review, exact SQLite/catalog validation, and exact iOS compatibility remain independent gates.

## Trusted events and permissions

- **HF-WORKFLOW-005:** Pull-request validation never runs credential-bearing acquisition or repository-write jobs. `pull_request_target` is forbidden for catalog workflow code.
- **HF-WORKFLOW-006:** Scheduled/manual acquisition and catalog mutation run only from reviewed default-branch workflow code. Untrusted PR/fork refs cannot select secret names, environments, commands, hosts, output paths, or write refs. Trusted scheduled, release, and health entrypoints fail closed when manually dispatched from a non-`main` ref.
- **HF-WORKFLOW-007:** Workflow permissions are explicit and least privilege. Ordinary validation/acquisition defaults to `contents: read`; write capabilities are confined to the proposal, health, and release jobs that actually need them.
- **HF-WORKFLOW-008:** Ordinary fixture validation requires no third-party secret, Central CI, self-hosted runner, signing identity, backend, or manually created `GITHUB_TOKEN` secret.
- **HF-WORKFLOW-009:** Every external GitHub Action reference introduced by this workflow framework is pinned to a reviewed full commit SHA. Checkout does not persist Git credentials unless a narrowly reviewed write step requires an authenticated checkout.

## Artifact handoff contract

Every cross-stage payload has a v1 handoff envelope with artifact kind, registered source/snapshot identity, producer repository/commit/workflow/run identity, relative payload path, SHA-256, byte count, record count, completeness, redistribution class, optional content-schema version, and UTC creation timestamp.

- **HF-WORKFLOW-010:** Handoff payload paths are relative, traversal-free, and bounded. Producer SHA, workflow/run identity, digest, counts, timestamp, and enums fail closed on malformed or unsupported values.
- **HF-WORKFLOW-011:** A consumer validates artifact kind, configured byte/record ceilings, allowed redistribution class, declared completeness, and actual payload digest/byte count before use.
- **HF-WORKFLOW-012:** A stage that requires a complete snapshot rejects `partial`; a partial/truncated acquisition can be retained for diagnostics but cannot replace accepted complete state or enter a complete-required consumer.
- **HF-WORKFLOW-013:** Restricted source data is never uploaded or published under an artifact kind that permits only redistributable content. Public repository visibility does not override source rights.
- **HF-WORKFLOW-014:** Caches are performance aids only and are never the catalog source of truth. Cross-job state is identified by immutable handoff metadata and digest.

## Source admission and acquisition

- **HF-WORKFLOW-015:** A source key must be present, enabled, and versioned in the source registry before the reusable acquisition interface accepts it. The initial framework admits only the committed `synthetic-fixture`; production source issues add their own reviewed registrations.
- **HF-WORKFLOW-016:** The source contract records access method, source class, credential requirement, redistribution class, allowed hosts, and adapter version. Credentials and authenticated URLs never enter the registry or handoff.
- **HF-WORKFLOW-017:** Acquisition mode is an allowlisted bounded identifier. Fixture sources cannot be silently promoted to sample/full network acquisition.
- **HF-WORKFLOW-018:** Retry behavior is bounded and deterministic. Adapters may respect a longer explicit upstream `Retry-After`, but retries never convert a truncated/invalid response into a complete snapshot.

## Scheduling, proposal, health, and recovery

- **HF-WORKFLOW-019:** Scheduled refresh uses an off-hour minute, also exposes `workflow_dispatch`, declares concurrency, and is safe to rerun for the same source/snapshot. Public-repository schedule delay/automatic inactivity disablement is documented as an operational condition, not treated as evidence of a successful refresh.
- **HF-WORKFLOW-020:** Catalog proposal branch identity is derived deterministically from source/snapshot/catalog digest. Re-running an identical proposal targets the same bounded logical update and material changes are never auto-merged.
- **HF-WORKFLOW-021:** Health conditions use deterministic source/condition keys so repeated failures update one logical incident instead of creating unbounded duplicate issues. Health reporting must not expose source secrets or raw restricted payloads.
- **HF-WORKFLOW-022:** Release reports/checksums are post-merge evidence. The generated bundle files are intentionally not committed, so the v1 release job must materialize the SQLite/manifest pair from the exact integrated `main` revision and the same reviewed local catalog input before validating or hashing those subjects; it must never assume ignored generated files already exist after checkout. The v1 release workflow does not perform App Store signing and does not authorize separately downloaded runtime catalogs; the app continues to use the accepted bundled SQLite model unless a future specification changes it. An optional manual main-only provenance hook may attest subjects materialized and revalidated from that exact integrated revision with GitHub artifact attestations; ordinary fixture validation and normal release evidence do not require attestation permissions or a successful attestation.

## Security and data boundaries

Workflow framework code treats all external source fields as hostile data. Issue #23 owns the broader parser/network/file/OCR hardening gate, but this issue establishes the outer execution boundary: bounded identifiers, relative paths, explicit hosts, digest/count/completeness checks, no source-controlled secrets, no PR write authority, and no dynamic shell command selection.

Product images remain HTTPS references only under specifications 014/015. None of these workflow interfaces acquires or bundles product image bytes merely to classify, select, build, or validate a product.

No workflow stage may create `halal-certified` or `halal-reviewed` from acquisition/parser success. Assessment meaning stays governed by specifications 004/013 and the explicit human/evidence review lane.

## Acceptance tests

The committed tests cover:

- contract stage/source/artifact consistency and unknown future versions;
- source registration and fixture-only mode enforcement;
- payload path traversal, digest, byte/record limits, completeness, and redistribution class;
- deterministic proposal and health keys;
- pinned action references and absence of self-hosted/`pull_request_target` execution;
- trusted workflow isolation from `pull_request` and manual non-`main` execution;
- scheduled workflow + manual dispatch policy;
- release materialization of the intentionally ignored generated SQLite/manifest subjects before release validation;
- optional pinned main-only release provenance attestation without making it a fixture-validation prerequisite;
- no-secret synthetic handoff validation; and
- integration of workflow-contract validation into the existing GitHub-hosted Catalog integrity lane.
