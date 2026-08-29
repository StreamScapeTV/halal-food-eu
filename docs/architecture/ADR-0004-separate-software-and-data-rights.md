# ADR-0004 — Separate software and catalog-data rights

**Status:** Accepted  
**Date:** 2026-08-26

## Context

The owner wants publicly visible source while prohibiting commercial use and unauthorized deployment. That restriction is incompatible with the Open Source Definition. Product databases can also carry independent EU database rights and licenses such as the Open Database License.

## Decision

- License application source under the custom Halal Food EU Source-Available License.
- Describe the project as source-available, not OSI open source.
- Prohibit commercial use, production/public/private deployment, hosted services, and binary distribution without written permission.
- Exclude separately licensed catalog data and third-party material from the software license.
- Make each catalog manifest declare its data license and attribution.
- Import only sources whose collection and redistribution terms are documented and compatible.

## Consequences

Public source can still be copied technically, but unauthorized use is not licensed. The custom license should receive professional legal review before commercial enforcement. If Open Food Facts data is bundled, its ODbL rights and obligations apply to that database even though the app source remains source-available.
