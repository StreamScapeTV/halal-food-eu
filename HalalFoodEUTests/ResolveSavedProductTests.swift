import Foundation
import Testing
@testable import HalalFoodEU

@Suite("Saved product current-catalog resolution")
struct ResolveSavedProductTests {
    @Test("A newer catalog with the same product record does not claim a product change")
    func unchangedRecordAcrossCatalogVersions() async throws {
        let barcode = try Barcode(validating: "0200000000004")
        let original = product(barcode: barcode, name: "Fixture Oat Drink", catalogVersion: "v1")
        let current = product(barcode: barcode, name: "Fixture Oat Drink", catalogVersion: "v2")
        let reference = favoriteReference(product: original)
        let resolver = ResolveSavedProduct(
            catalog: FixedSavedProductCatalog(product: current),
            currentCatalogVersion: "v2"
        )

        let resolved = try await resolver(reference)

        #expect(resolved.changeState == .unchanged)
        #expect(resolved.catalogVersionChanged)
        #expect(resolved.currentProduct == current)
    }

    @Test("A material product change is reported independently from catalog version")
    func changedRecord() async throws {
        let barcode = try Barcode(validating: "0200000000004")
        let original = product(barcode: barcode, name: "Fixture Oat Drink", catalogVersion: "v1")
        let current = product(barcode: barcode, name: "Changed Oat Drink", catalogVersion: "v2")
        let resolver = ResolveSavedProduct(
            catalog: FixedSavedProductCatalog(product: current),
            currentCatalogVersion: "v2"
        )

        let resolved = try await resolver(favoriteReference(product: original))

        #expect(resolved.changeState == .changed)
        #expect(resolved.catalogVersionChanged)
    }

    @Test("A previously present product can disappear from the current catalog")
    func productNoLongerPresent() async throws {
        let barcode = try Barcode(validating: "0200000000004")
        let original = product(barcode: barcode, name: "Fixture Oat Drink", catalogVersion: "v1")
        let resolver = ResolveSavedProduct(
            catalog: FixedSavedProductCatalog(product: nil),
            currentCatalogVersion: "v2"
        )

        let resolved = try await resolver(favoriteReference(product: original))

        #expect(resolved.changeState == .noLongerPresent)
        #expect(resolved.currentProduct == nil)
        #expect(resolved.currentCatalogVersion == "v2")
    }

    @Test("A previously missing camera scan can become available later")
    func previouslyMissingNowAvailable() async throws {
        let barcode = try Barcode(validating: "0200000000004")
        let entry = ScanHistoryEntry(
            id: 1,
            barcode: barcode,
            scannedAt: Date(timeIntervalSince1970: 1_700_000_000),
            catalogVersion: "v1",
            versionMarker: SavedProductVersionMarker(product: nil)
        )
        let current = product(barcode: barcode, name: "Fixture Oat Drink", catalogVersion: "v2")
        let resolver = ResolveSavedProduct(
            catalog: FixedSavedProductCatalog(product: current),
            currentCatalogVersion: "v2"
        )

        let resolved = try await resolver(SavedProductReference(historyEntry: entry))

        #expect(resolved.changeState == .nowAvailable)
        #expect(resolved.currentProduct == current)
    }

    private func favoriteReference(product: ProductRecord) -> SavedProductReference {
        SavedProductReference(
            favorite: FavoriteProduct(
                barcode: product.barcode,
                savedAt: Date(timeIntervalSince1970: 1_700_000_000),
                catalogVersion: product.catalogVersion,
                versionMarker: SavedProductVersionMarker(product: product)
            )
        )
    }

    private func product(
        barcode: Barcode,
        name: String,
        catalogVersion: String
    ) -> ProductRecord {
        ProductRecord(
            barcode: barcode,
            name: name,
            brand: "Fixture Brand",
            observation: nil,
            assessment: .unreviewedUnknown,
            catalogVersion: catalogVersion,
            details: ProductRecordDetails(
                market: "DE",
                brandOwner: nil,
                quantity: "1 L",
                conflictFlags: [],
                retailerEvidence: [],
                remoteImages: []
            )
        )
    }
}

private actor FixedSavedProductCatalog: ProductCatalog {
    let product: ProductRecord?

    init(product: ProductRecord?) {
        self.product = product
    }

    func product(for barcode: Barcode) async throws -> ProductRecord? {
        product
    }
}
