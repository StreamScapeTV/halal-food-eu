# Security policy

## Supported versions

Until the first production release, only the current `main` branch receives security fixes.

## Reporting

Do not publish vulnerabilities involving catalog tampering, arbitrary SQL execution, unsafe URL handling, camera privacy, signature verification, or a false halal/not-halal conclusion caused by exploitable behavior. Open a private security advisory in the GitHub repository when available, or contact the repository owner privately through their GitHub profile.

Include reproduction steps, affected commit, impact, and a minimal proof of concept. Do not include real consumers’ personal data.

## Security principles

- Barcode scanning and lookup are local by default.
- Camera frames are not persisted or transmitted.
- SQL uses prepared statements and a read-only bundled database.
- Catalog bytes are checked against a manifest digest before release.
- Production catalog provenance and license metadata are mandatory.
- No secrets, API keys, or signing credentials belong in the repository.
