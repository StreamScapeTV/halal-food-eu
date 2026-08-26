import Foundation
import Testing
@testable import HalalFoodEU

@Suite("Bundled SQLite catalog")
struct CatalogIntegrationTests {
    @Test("Loads a reviewed product and ordered reasons from the real SQLite resource")
    func loadsKnownProduct() async throws {
        let catalog = try makeCatalog()
        let barcode = try Barcode(validating: "0200000000004")
        let product = try #require(try await catalog.product(for: barcode))

        #expect(product.name == "Demonstration Oat Drink")
        #expect(product.assessment.status == .halalReviewed)
        #expect(product.assessment.reasons.map(\.code) == [
            "DEMO-INGREDIENTS-REVIEWED",
            "NO-CERTIFICATION-CLAIM",
        ])
        #expect(product.catalogVersion == "0.1.0-demo.1")
    }

    @Test("A valid absent GTIN returns nil instead of an unknown product")
    func returnsNilForAbsentProduct() async throws {
        let catalog = try makeCatalog()
        let barcode = try Barcode(validating: "0200000000035")
        let product = try await catalog.product(for: barcode)
        #expect(product == nil)
    }

    private func makeCatalog() throws -> SQLiteProductCatalog {
        let bundle = Bundle(for: TestBundleToken.self)
        let databaseURL = try #require(
            bundle.url(forResource: "catalog", withExtension: "sqlite3"),
            "catalog.sqlite3 must be copied into the unit-test bundle"
        )
        return try SQLiteProductCatalog(databaseURL: databaseURL)
    }
}

private final class TestBundleToken {}
