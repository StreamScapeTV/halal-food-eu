# ADR-0002 — Immutable bundled SQLite catalog

**Status:** Accepted  
**Date:** 2026-08-26

## Context

Core product lookup must work without a server or network. The dataset may grow to many products and requires exact GTIN lookup, provenance, dated observations, assessment reasons, and future indexed search. JSON loading would increase launch/memory cost and make indexed queries difficult.

## Decision

- Build a SQLite database outside the app and package it as a resource.
- Open it read-only at runtime behind a `ProductCatalog` protocol.
- Use normalized GTIN-14 as a unique primary key.
- Keep observations immutable and bind assessments to observation IDs.
- Validate integrity, foreign keys, schema/application IDs, logical content, and query plans before release.
- Isolate the connection in an actor and expose async repository methods.
- Keep mutable user data in a separate future store.

## Consequences

Exact lookup is small and indexed, the app has no server dependency, and a catalog update initially requires an app release. SQLite schema changes must be coordinated with app compatibility. Future signed delta downloads require a new ADR and specification.
