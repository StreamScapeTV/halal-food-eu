# Security policy

## Supported versions

Until the first production release, only the current `main` branch receives security fixes.

## Reporting

Do not publish vulnerabilities involving catalog tampering, arbitrary SQL execution, unsafe URL handling, camera privacy, signature verification, source/workflow credential exposure, dependency compromise, or a false halal/not-halal conclusion caused by exploitable behavior. Open a private security advisory in the GitHub repository when available, or contact the repository owner privately through their GitHub profile.

Include reproduction steps, affected commit, impact, and a minimal proof of concept. Do not include real consumers’ personal data, restricted source payloads, credentials, authenticated URLs, or secret canaries.

## Security principles

- Barcode scanning and lookup are local by default.
- Camera frames are not persisted or transmitted.
- SQL uses prepared statements and a read-only bundled database.
- Catalog bytes are checked against the bundled manifest before runtime lookup and before release.
- Catalog manifests bind the reviewed source-policy schema/version and SHA-256.
- Production catalog provenance and license metadata are mandatory.
- Untrusted parser/network/archive inputs are bounded and fail closed.
- External workflow actions and XcodeGen are pinned to reviewed immutable commits; Python catalog tooling remains standard-library-only.
- Product image bytes are not part of the current catalog contract.
- No secrets, API keys, private feed tokens, signing credentials, or user data belong in the repository.

## Incident response

Use [the catalog security incident-response playbook](docs/security/catalog-incident-response.md) for containment, evidence hygiene, rollback, and recovery gates.
