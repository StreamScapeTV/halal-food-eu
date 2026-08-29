# Halal Food EU

Halal Food EU is a privacy-first iPhone application for scanning packaged-food barcodes and showing an evidence-based halal assessment from a versioned SQLite catalog bundled with the app.

The core flow is deliberately offline:

1. scan an EAN/UPC barcode or enter it manually;
2. normalize it to a validated GTIN-14;
3. execute an indexed local SQLite lookup; and
4. show the status, reasons, ingredient snapshot, source, and dates.

## Current state

The repository contains the initial iOS foundation, canonical feature specifications, a deterministic catalog builder, a synthetic demonstration catalog, unit/integration tests, and GitHub-hosted CI. The bundled records are examples only and do not represent real retail products.

The accepted product and engineering requirements live in [`docs/feature-specs`](docs/feature-specs/README.md). Architecture decisions live in [`docs/architecture`](docs/architecture/README.md).

## Platform and architecture

- iPhone only
- iOS 18.0 minimum
- current stable Xcode 26 toolchain
- Swift 6 with complete strict concurrency checking
- native SwiftUI controls
- VisionKit barcode scanning with manual-entry fallback
- no runtime third-party dependencies
- no account, analytics, ads, backend, or network requirement
- immutable bundled SQLite catalog behind a domain repository

Building with Xcode 26 allows standard controls to adopt the system-provided Liquid Glass design on iOS 26 while the same SwiftUI code keeps the native iOS 18 appearance.

## Development

Requirements: macOS, stable Xcode 26, Homebrew, Python 3, and [XcodeGen](https://github.com/yonaskolb/XcodeGen).

```bash
brew install xcodegen
make generate
open HalalFoodEU.xcodeproj
```

Run the full local validation:

```bash
make catalog-validate
./Scripts/ci-ios.sh
```

Rebuild the synthetic catalog after changing `Data/sample-products.json`:

```bash
make catalog
```

The generated `.xcodeproj` is intentionally not committed; `project.yml` is the deterministic project source of truth.

## Demonstration barcodes

The synthetic catalog includes valid restricted-circulation example GTINs:

- `0200000000004` — reviewed-halal demonstration oat drink
- `0200000000011` — not-halal demonstration gelatine sweets
- `0200000000028` — questionable demonstration dessert

These are not claims about real products.

## Data sourcing

A public product page is not automatically reusable data. Real records may be included only when collection and redistribution are permitted and provenance is retained. Open Food Facts is a possible source, but its ODbL attribution/share-alike duties apply to a derived database. Retailer data such as Lidl, REWE, or EDEKA requires an approved feed/API or explicit permission for the intended redistribution.

Read [`DATA_LICENSE.md`](DATA_LICENSE.md) before adding or reusing catalog data.

## Licensing

This repository is **source-available, not OSI open source**. The custom [`LICENSE`](LICENSE) allows study and contribution development but forbids commercial use, production/public/private deployment, hosted services, and binary distribution without written permission.

Public visibility cannot technically prevent copying. The license establishes the permissions and legal restrictions; it is not a technical copy-protection mechanism. The custom text should be reviewed by a qualified lawyer before relying on it for enforcement or a commercial licensing program.

Catalog data and third-party materials can have separate licenses identified by their manifest and notices.

## Important limitation

Halal Food EU is an informational evidence tool. It is not a fatwa, certification, allergy guarantee, or substitute for checking current packaging, manufacturers, recognized certifiers, and a trusted qualified scholar. Formulations and supply chains change.
