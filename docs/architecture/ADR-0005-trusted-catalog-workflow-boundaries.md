# ADR-0005 — Trusted catalog workflow boundaries

**Status:** Accepted  
**Date:** 2026-08-29

## Context

Halal Food EU needs repeatable public-data acquisition and catalog updates without turning one scheduled workflow into a privileged monolith. The repository is public, normal app behavior is offline, future sources may have different redistribution/credential rules, and raw data must be treated as hostile. Pull requests must still validate the framework without receiving source credentials or write authority.

## Decision

Use small GitHub-hosted Actions workflows coordinated through a versioned, digest-bound artifact envelope.

1. Source policy, acquisition, normalization/diff, quality, build, proposal, iOS validation, release, and health remain separate logical stages.
2. Repository Python tooling owns non-trivial validation and deterministic IDs; workflow YAML is thin orchestration.
3. Cross-stage payloads carry source/snapshot/producer identity, SHA-256, byte/record counts, completeness, and redistribution class. Complete-required stages reject partial artifacts.
4. PR/fork validation uses committed fixtures only. Trusted acquisition/write workflows never use `pull_request_target` and do not run from arbitrary PR code.
5. GitHub-hosted runners and the job-scoped `GITHUB_TOKEN` are sufficient for the framework. No Central CI, self-hosted runner, PAT, backend, or third-party secret is required by fixture validation.
6. Write permissions are restricted to generated proposal, health, and post-merge release jobs. Material catalog changes always remain reviewable; no workflow auto-merges them.
7. The initial source registry contains only `synthetic-fixture`. Production source issues extend source admission explicitly instead of gaining access through dynamic command/URL inputs.
8. The app remains a read-only bundled-SQLite consumer. Workflow orchestration does not add runtime network dependency or image binaries.

## Consequences

Adding a source requires a reviewed registry/adapter change, but cannot duplicate or bypass common build/release gates. Large source payload transport can evolve behind the same handoff contract, including restricted retention rules, without changing iOS runtime architecture. A failed or partial acquisition is diagnosable but cannot silently replace accepted state. Security hardening in #23 can strengthen parsers/network/artifact provenance without redefining the stage boundaries established here.

The repository keeps generated updates deterministic through proposal and health keys, which makes reruns idempotent and prevents unbounded duplicate branches/issues. Source-specific correctness, real-data scale, storage architecture, and final production SQLite compilation remain owned by #8/#9, #30, and #12 rather than being hidden inside this workflow issue.
