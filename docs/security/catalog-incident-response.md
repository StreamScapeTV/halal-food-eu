# Catalog security incident response

This playbook applies to catalog acquisition/build/release infrastructure and the bundled iOS catalog. Do not put secrets, private user data, restricted source payloads, or exploit payloads into public issue comments or CI logs.

## Immediate containment

For a suspected credential exposure, compromised dependency/action, malicious or prohibited source payload, catalog tamper, incorrect safety assessment, source/license revocation, or unexpected PII:

1. Stop the affected trusted acquisition/proposal/release entrypoint or source registration from producing new accepted material.
2. Preserve immutable identifiers only: repository commit, workflow/run ID, source key/snapshot ID, artifact kind, SHA-256, and timestamps. Restricted/raw payloads stay in their allowed retention boundary.
3. Rotate or revoke exposed credentials outside Git. Never paste the old or replacement value into issues, pull requests, logs, artifacts, or chat.
4. Mark affected catalog evidence as unsafe for promotion. A partial, malformed, unlicensed, or provenance-uncertain snapshot cannot replace the last accepted complete snapshot.
5. Open or update one bounded security incident using non-sensitive facts. Use a private GitHub security advisory for exploit details or secret-bearing evidence.

## Triage by incident class

- **Dependency/action compromise:** pin or remove the dependency, verify the reviewed manifest, rebuild from an unaffected commit, and inspect all jobs that had write/token authority.
- **Source compromise or license revocation:** disable the source registration, quarantine affected snapshots, identify every derived catalog digest/version, and re-run selection/assessment from an admitted source or last-known-good snapshot.
- **Catalog database/manifest tamper:** reject the pair, compare SHA-256 and source-policy identity to release evidence, rebuild from reviewed local inputs, and ship a new app/catalog release containing the last known-good logical content.
- **Incorrect halal/not-halal assessment:** treat it as a consumer-safety incident. Remove reassuring language if evidence is stale/conflicting, preserve historical review meaning, re-review under the versioned methodology, and document the correction without claiming certification that is not evidenced.
- **Prohibited/PII/image data:** prevent publication, delete retained copies where policy and legal obligations permit, review logs/artifacts for propagation, and add a regression fixture that contains no real personal or restricted data.
- **SSRF/path/archive/parser exploit:** disable the affected adapter/path, preserve only bounded diagnostic metadata, add a minimized synthetic regression case, and confirm the fix rejects equivalent variants before restoring acquisition.

## Recovery gate

Recovery is complete only after the exploit/cause has a regression test, reviewed dependency/source policy is current, catalog/evidence checks are green, no secret canary appears in outputs, exact candidate CI is green, and the integrated `main` result is revalidated. Material catalog changes remain human-reviewable and are never auto-merged because a security check passed.

For bundled-catalog rollback, publish a new application build containing the last known-good catalog or a reviewed corrected successor. The current architecture does not authorize a separately downloaded runtime catalog or remote hot patch.

## Communication and evidence hygiene

Public status may name the incident class, affected versions/digests, impact, containment state, and remediation commit. Do not publish credentials, authenticated URLs, raw restricted payloads, private customer submissions, or exploit details that would materially increase risk before remediation. Follow `SECURITY.md` for private vulnerability reporting.
