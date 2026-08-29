# Architecture

Halal Food EU uses a small clean architecture optimized for one iOS application and an immutable offline catalog. The design avoids both God objects and needless framework layers.

## Dependency direction

```text
SwiftUI Features ──> Domain Use Cases ──> Domain Repository Protocols
       │                                         ▲
       └──────── App composition root ───────────┤
                                                 │
                                      SQLite Data Implementation
```

- **App** constructs dependencies and owns application-level composition.
- **Domain/Models** contains immutable `Sendable` business values.
- **Domain/Repositories** defines boundaries without SQLite, SwiftUI, or camera types.
- **Domain/UseCases** validates inputs and coordinates one business action.
- **Data/SQLite** owns SQLite handles, SQL, row mapping, and catalog compatibility.
- **Features** owns `@MainActor` observable state and native SwiftUI views.
- **Tools** builds, hardens, and validates the bundled database outside the application.

## Patterns used intentionally

- Repository pattern at the catalog boundary.
- Composition root / dependency injection in `AppContainer`.
- Factory methods for live and test construction.
- Use-case object for barcode parsing plus lookup.
- Immutable observation and assessment snapshots.
- Actor isolation for SQLite ownership.

Patterns are not goals by themselves. A new abstraction must remove meaningful coupling, enable testing, or encode an invariant.

## Runtime data flow

1. VisionKit or manual entry emits a payload.
2. `BarcodePayloadParser` extracts and validates a normalized GTIN-14.
3. `LookupProductByBarcode` asks `ProductCatalog` asynchronously.
4. `SQLiteProductCatalog` validates the manifest/database pair once, then executes indexed prepared queries on its actor.
5. Immutable domain values return to the `@MainActor` view model.
6. SwiftUI displays status, evidence, freshness, source, and dates.

## Current ADRs

- [ADR-0001 — iOS 18 and native SwiftUI](ADR-0001-ios-18-native-swiftui.md)
- [ADR-0002 — Immutable bundled SQLite](ADR-0002-immutable-bundled-sqlite.md)
- [ADR-0003 — Evidence-first assessment states](ADR-0003-evidence-first-assessments.md)
- [ADR-0004 — Separate software and data rights](ADR-0004-separate-software-and-data-rights.md)
- [ADR-0005 — Trusted catalog workflow boundaries](ADR-0005-trusted-catalog-workflow-boundaries.md)
- [ADR-0006 — Secure catalog ingestion and runtime integrity](ADR-0006-secure-catalog-ingestion-and-integrity.md)
