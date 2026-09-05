# 028 — Central Apple validation and TestFlight release boundary

**Status:** Accepted  
**Last reviewed:** 2026-09-05

## Purpose

Halal Food EU may reuse the organization-wide product-neutral Apple workflow for optional macOS validation and trusted TestFlight publication. Product commands, catalog materialization, release identity, archive/export behavior, and package verification remain owned by this repository. The existing GitHub-hosted push and pull-request lanes remain the mandatory independent correctness path.

## Accepted requirements

- **HF-APPLE-CI-001:** Ordinary repository push and pull-request validation continues to run on GitHub-hosted runners without Central CI, Agent State, signing credentials, or an App Store Connect dependency. Central Apple execution is additive and optional.
- **HF-APPLE-CI-002:** A product-owned manual caller may invoke only a reviewed immutable SHA of `StreamScapeTV/ci-workflows/.github/workflows/apple.yml`. Mutable reusable-workflow refs such as `@main` are forbidden.
- **HF-APPLE-CI-003:** Hosted validation uses the tracked non-symlink `scripts/ci/run-apple-hosted-validation.sh` boundary and accepts only the fixed `build`, `test`, or `simulator` semantic profile from `CI_APPLE_HOSTED_PROFILE`. The wrapper must bootstrap the reviewed Xcode/XcodeGen contract and reuse `Scripts/ci-ios.sh`; it must not accept arbitrary shell commands, script paths, schemes, or catalog paths from Central.
- **HF-APPLE-CI-004:** The default `Scripts/ci-ios.sh` behavior remains the full GitHub-hosted test path. A product-internal validation selector may map Central `build` to a signing-disabled simulator build and `test`/`simulator` to the existing bounded simulator test path, without changing catalog evidence semantics.
- **HF-APPLE-CI-005:** TestFlight publication is permitted only for an exact protected-`main` source revision, an explicit caller-supplied valid `CFBundleVersion`, and a validated production catalog release receipt. A synthetic/demo fixture, missing production receipt, mismatched catalog source revision, or non-main source must fail before archive or upload.
- **HF-APPLE-CI-006:** The TestFlight wrapper consumes only the fixed Central `CI_APPLE_TESTFLIGHT_*` release context. It must use the caller build number unchanged and must fail rather than derive, increment, retry with, or substitute another build number.
- **HF-APPLE-CI-007:** The catalog packaged for TestFlight must come from successful `catalog-release.yml` release evidence for the same exact application source SHA. The wrapper revalidates the production SQLite/manifest pair before archive and verifies the same catalog and manifest SHA-256 values inside both the signed archive and exported IPA before upload.
- **HF-APPLE-CI-008:** Signing/export/upload authentication is temporary runner state supplied by Central. Product tooling must not commit credentials, echo private-key contents, persist credentials in the repository, or leave a second product-owned credential store after the Central cleanup boundary.
- **HF-APPLE-CI-009:** A real TestFlight upload is an external release proof, not a prerequisite for implementing the fail-closed wrapper while production catalog evidence or owner-authorized signing credentials are unavailable. The first authorized upload must still prove the complete exact-source/build/catalog contract before the release lane is considered operationally proven.
- **HF-APPLE-CI-010:** Future downloadable market modules from specifications 012/025/026 or issue #72 do not silently change this release boundary. Until a later accepted specification explicitly promotes that model, TestFlight packages the accepted bundled production catalog.

## Validation

- Wrapper scripts parse under the repository shell baseline and reject missing/unsupported fixed context before invoking expensive tooling.
- Repository workflow policy accepts immutable SHA-pinned reusable workflows and rejects mutable reusable-workflow refs.
- A GitHub-hosted contract test covers the wrapper shape and synthetic-catalog rejection without requiring macOS or release secrets.
- At least one exact-source Central hosted validation profile must execute successfully before this integration is considered implemented.
- A real TestFlight upload remains contingent on an accepted production release receipt and owner-authorized App Store Connect signing credentials.
