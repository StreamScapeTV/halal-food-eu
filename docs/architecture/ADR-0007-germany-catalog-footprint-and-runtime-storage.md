# ADR-0007 — Germany catalog footprint and runtime storage

**Status:** Accepted  
**Date:** 2026-08-30

## Context

ADR-0002 chose an immutable bundled SQLite catalog so exact barcode lookup works fully offline. Issue #30 tests that decision against a current Germany-sized catalog before production compilation (#12), including complete runtime semantic overhead rather than only product rows. The benchmark keeps operational/raw history outside the iOS bundle, preserves source/license partitions, avoids fabricating evidence for products whose ingredients are missing, and measures the compact basic-exclusion index requested by HF-DB-017 / HF-SELECTION-013.

The accepted budgets are a bundled Germany runtime catalog below **250 MiB**, indexed exact lookup p95 below **50 ms** warm, and first lookup after open below **150 ms** on the accepted release-device benchmark. GitHub-hosted Linux measurements below are architecture evidence for storage/query scaling, not a substitute for later release-iPhone performance acceptance.

## Evidence

Exact benchmark implementation: `67940f25982db5107a91046d13fff1e4eecab42f`. Exact workflow run: `33292878492`. Uploaded report artifact digest: `sha256:65afd4ac936daaf056da61b148ae8232322a0803ac4e8f7c47777a3cab9a4868`.

### Source snapshots

- Open Food Facts snapshot `off-benchmark-33292878492-1`, retrieved `2026-08-30T04:36:51Z`. Transport SHA-256 `414bbb7796ef997f35c26428d554dfc1191e4a89f509242c459589459ec2f68e`; 12,773,904,967 compressed bytes / 81,159,318,854 expanded bytes scanned; 4,714,849 records examined and 53,774 supported-schema Germany records emitted. Upstream schema distribution: 1001=517,271, 1002=535,944, 1003=2,206,652, 1004=529,716, missing=925,266. Source-policy SHA-256 `52df981c11be442efdc8a85959b8fb4411f7a6c4c52eb38271b7fac701f14a95`; selection-policy version `1.0.0` and SHA-256 `d9ad6d4cce9cd7c03f64e19206be5d7d0a70466386de1fd68d9bce0578c515d0`.
- Open Prices snapshot `op-benchmark-33292878492-1`, retrieved `2026-08-30T04:57:07Z`. Official exports total 30,189,152 compressed bytes (locations=1,012,959 B, prices=20,427,306 B, proofs=8,748,887 B); normalized projected payload 140,901,934 bytes. Source-policy SHA-256 `131d1d78d5ddeff63f0f2dad536c05cf73bafd857c77b6f212af578a46bb4c5e`; alias version `1.0.0` / SHA-256 `85f2adc72bc5c10a24f40e0ab67ab65b1435f5c5eb9ffc559430c60faf9e215e`.
- Selection policy v1 examined **53,774** normalized Germany source records, retained **53,205** unique valid GTINs, rejected **415** invalid/unsupported barcode records, and moved **154** explicitly approved basic products into the compact exclusion set. The detailed logical payload is **38,767,258 bytes** before SQLite.
- The selected Germany catalog contains **53,205** unique valid GTINs. Ingredient evidence exists for **25,350** and is missing for **27,855** (47.646% coverage). Missing ingredients remain missing evidence; they are never materialized as empty ingredient observations.
- The complete current Open Prices export was acquired and measured, but the admitted Germany retailer projection produced **0** accepted observation rows over **0** GTINs, so no retailer summary rows enter this runtime snapshot. The normalizer placed **169 distinct retailer-alias entries** into its review queue on this exact run; those entries are intentionally not guessed into retailer identities. This is a measured coverage/aliasing limitation, not evidence that Germany has no Open Prices data, and no completeness or current-stock claim is made.
- No unauthorized retailer scraping and no product image binaries were used.

The source semantic SHA-256 `732a2cf01e2c218b36fc4d8280ded2fa1cfb47a2e05ca026a952a0a354190339` equals both the current/audit round-trip SHA-256 `732a2cf01e2c218b36fc4d8280ded2fa1cfb47a2e05ca026a952a0a354190339` and compact runtime round-trip SHA-256 `732a2cf01e2c218b36fc4d8280ded2fa1cfb47a2e05ca026a952a0a354190339`. Storage choices therefore preserve the benchmarked real-product semantics.

### Complete runtime model

| Scale | Products | Ingredients | Assessments | Reasons | Certifications | SQLite after `VACUUM` | gzip | Index overhead | Build + measure | Warm exact GTIN p95 | Product-detail p95 | Open + lookup p95 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1x | 53,205 | 25,350 | 53,205 | 72,234 | 6,390 | 73.25 MiB | 14.27 MiB | 8.50 MiB | 4.094 s | 0.0151 ms | 0.0487 ms | 0.2238 ms |
| 2x | 106,410 | 50,700 | 106,410 | 144,399 | 12,658 | 137.95 MiB | 22.63 MiB | 15.62 MiB | 7.756 s | 0.0152 ms | 0.0477 ms | 0.2154 ms |
| 5x | 266,025 | 126,750 | 266,025 | 361,104 | 31,740 | 332.23 MiB | 47.69 MiB | 37.00 MiB | 18.354 s | 0.0158 ms | 0.0499 ms | 0.2082 ms |

The 1x benchmark-only assessment distribution is halal-certified=6,390, halal-reviewed=6,321, not-halal=6,371, questionable=6,268, unknown=27,855. These rows exercise accepted status/reason/certification storage overhead without classifying the real products. Products without ingredient evidence receive only a benchmark `unknown` assessment and a NULL ingredient pointer.

Product detail remains the accepted two-query path: one product/ingredient/assessment/certification/source query and one ordered-reasons query. The 1x detail p95 is **0.0487 ms** on the GitHub-hosted Linux runner. Exact GTIN plan: `(2, 0, 0, 'SEARCH products USING PRIMARY KEY (gtin=?)')`. Detail plan: `(7, 0, 0, 'SEARCH p USING PRIMARY KEY (gtin=?)'); (13, 0, 0, 'SEARCH i USING PRIMARY KEY (id=?) LEFT-JOIN'); (20, 0, 0, 'SEARCH a USING PRIMARY KEY (id=?) LEFT-JOIN'); (27, 0, 0, 'SEARCH c USING INDEX idx_certifications_assessment (assessment_id=?) LEFT-JOIN'); (36, 0, 0, 'SEARCH s USING PRIMARY KEY (source_key=?) LEFT-JOIN')`. Reason plan: `(3, 0, 0, 'SEARCH assessment_reasons USING PRIMARY KEY (assessment_id=?)')`.

### Basic-exclusion index

The accepted v1 basic-exclusion index contains **154** rows and only GTIN, market, selection-policy version, and stable exclusion reason. Its measured incremental cost is **8,192 bytes** vacuumed and **1,524 bytes** gzip; lookup p95 is **0.0128 ms**. Query plan: `(2, 0, 0, 'SEARCH basic_exclusion USING PRIMARY KEY (gtin=? AND market=?)')`.

This cost is negligible relative to the runtime catalog, so production #12 may ship the compact index with exactly the semantics allowed by HF-SELECTION-013. It must never contain ingredients, assessment states, certification records, detailed evidence, or a positive halal verdict.

### Operational, build, and multi-market scaling

- Current/audit SQLite: **28.96 MiB** vacuumed / **10.90 MiB** gzip; indexes add **2.44 MiB** before vacuum. It carries 25,350 real current ingredient rows and zero fabricated missing-ingredient rows.
- Raw/staged acquisition is intentionally outside the app bundle: OFF 12,773,904,967 compressed bytes / 81,159,318,854 expanded bytes scanned; Open Prices 30,189,152 compressed bytes / 140,901,934 normalized payload bytes.
- Germany-equivalent independent-market capacity model: 1 market = 73.25 MiB vacuumed / 14.27 MiB gzip, 2 markets = 146.51 MiB vacuumed / 28.54 MiB gzip, 5 markets = 366.27 MiB vacuumed / 71.36 MiB gzip. This is a sizing model, not permission to combine legally incompatible databases.
- End-to-end GitHub-hosted run from full OFF acquisition through report binding: **1287.7 s**. Peak observed runner-temp footprint: **346.17 MiB**. Max RSS: OFF normalization **2,024,576 KiB**, Open Prices normalization **166,624 KiB**, benchmark **1,017,100 KiB**. Environment: `ubuntu-latest`, `x86_64`, SQLite `3.45.1`, Python `3.12.3`.
- Representative seven-day freshness proxy: **4,509 product rows**, **0 retailer observations**, **360,081 canonical bytes**, window `2026-08-23T04:36:51Z` to `2026-08-30T04:36:51Z`. This is explicitly a single-snapshot timestamp proxy, not a two-snapshot binary delta.

## Decision

Keep **Option A — immutable bundled SQLite** as the production mobile runtime architecture. Do **not** add a runtime PostgreSQL/backend dependency for barcode lookup.

The complete 1x runtime projection is **73.25 MiB**, only **29.3%** of the accepted 250 MiB Germany budget, while exact indexed warm p95 is **0.0151 ms** and open+lookup p95 is **0.2238 ms** on Linux CI. The deterministic 2x semantic-storage model remains **137.95 MiB**, while the 5x model reaches **332.23 MiB** and deliberately crosses the current 250 MiB Germany planning threshold. That is a future partition/review trigger, not evidence for a network-first runtime. The current 1x catalog has ample headroom and the evidence does not justify placing availability, privacy, authentication, backup, or network operations on the scan path.

Under Option A:

1. The iOS app bundles only the compact current runtime projection required for lookup/result UX and traceability.
2. Raw source exports, superseded observations, review/package artifacts, build intermediates, and broad audit history remain workflow/operational data outside the app bundle.
3. Germany is the initial catalog partition. Additional markets are measured and legally reviewed independently; incompatible source/database rights stay in separate partitions unless legal review explicitly permits combination.
4. Production #12 may add the measured compact basic-exclusion index only within the four-field semantics allowed by HF-SELECTION-013.
5. A backend/PostgreSQL store may later be introduced for operational ingest, audit history, private-source staging, or internal review, but it is not on the mobile lookup path and must not weaken offline behavior.
6. Private/contracted source payloads, if admitted later, remain in private operational storage subject to their source terms. That can justify private PostgreSQL/object storage later, but not PostgreSQL on the scan path now.
7. Optional signed catalog delivery remains future issue #25. It must preserve a complete bundled fallback and requires its own accepted security/distribution design; #30 creates no runtime server dependency.

## Budgets and guards

- Bundled Germany runtime SQLite: **< 250 MiB** vacuumed baseline. A measured projection at or above this threshold requires architecture review before release.
- Exact indexed lookup: **< 50 ms p95 warm** on the accepted release-device benchmark; CI must preserve the indexed GTIN plan.
- First lookup after database open: **< 150 ms p95** on the accepted release-device benchmark.
- Product detail: keep the accepted two-query upper bound and retain indexed/bounded query plans.
- 5x growth must remain structurally valid; exceeding install-size or release-device performance budgets triggers partitioning/review before source or market expansion.
- No image binaries in SQLite. Remote image references remain optional metadata only where an accepted feature needs them.
- Source-policy/license identity and semantic round-trip hashes are release evidence. Current/audit/runtime projections must preserve the same real-product semantics.

## Freshness and update economics

The measured seven-day proxy shows catalog churn but is **not** a real two-snapshot binary delta, so it cannot establish exact update bandwidth. App Store-only refresh remains acceptable for the initial bundled-catalog milestone because #12 is not yet production-mature and the app must retain a complete bundled baseline. If actual release cadence later demonstrates that evidence freshness cannot tolerate App Store timing, #25 is the preferred architecture: signed, optional, atomically verified catalog assets with bundled fallback, rather than network-first lookup.

Choosing SQLite for runtime avoids standing up PostgreSQL now, so barcode lookup gets no new database service to back up, patch, expose, credential, monitor, or restore. If future private-feed ingestion/history makes a self-hosted operational store worthwhile, that follow-up must define least-privilege credentials, backup/restore tests, retention/takedown, schema migrations, incident response, and deterministic export into the same offline SQLite boundary.

## Rejected alternatives

### Runtime PostgreSQL / mandatory backend lookup

Rejected because it breaks the offline-first requirement, adds availability/privacy/operations cost, duplicates a lookup path already served efficiently by SQLite, and is not justified by current Germany runtime size or query evidence. PostgreSQL remains a possible operational store outside app runtime if future private feeds or retained history need it.

### Bundling raw/current audit history wholesale

Rejected. It inflates app size with data runtime does not need, can mix data-rights boundaries, and conflicts with the compact runtime-evidence contract. Runtime retains source/evidence traceability; operational storage retains richer audit/history material.

### One monolithic multi-market database by default

Rejected. Growth is measurable, but market/source licensing can differ. New markets default to independently built partitions until both size and legal compatibility are explicitly reviewed.

## Consequences

Production compiler #12 can proceed with a measured physical target rather than introducing a backend pre-emptively. The app remains fully functional offline. Build infrastructure owns large upstream acquisition and deterministic projection. Future growth is controlled by explicit size/performance/license gates rather than silently widening the bundle.

The Linux timings above are CI architecture evidence only. Final production acceptance still requires the release-device performance tests required by feature specification 009 and exact bundled-SQLite integration tests required by specification 011.
