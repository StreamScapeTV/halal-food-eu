import Foundation
import Testing
@testable import HalalFoodEU

private final class ReleaseReadinessRetailerBundleToken: NSObject {}

@Suite("Release-readiness retailer projection")
struct ReleaseReadinessRetailerProjectionTests {
    @Test("Retailer observation provenance survives the SQLite-to-domain projection")
    func loadsRetailerEvidenceProvenance() async throws {
        let bundle = Bundle(for: ReleaseReadinessRetailerBundleToken.self)
        let databaseURL = try #require(
            bundle.url(forResource: "catalog", withExtension: "sqlite3"),
            "catalog.sqlite3 must be copied into the unit-test bundle"
        )
        let manifestURL = try #require(
            bundle.url(forResource: "catalog-manifest", withExtension: "json"),
            "catalog-manifest.json must be copied into the unit-test bundle"
        )
        let catalog = SQLiteProductCatalog(databaseURL: databaseURL, manifestURL: manifestURL)
        let product = try #require(
            try await catalog.product(for: Barcode(validating: "0200000000028"))
        )
        let evidence = try #require(product.details?.retailerEvidence.first)

        #expect(evidence.kind == .retailerObservation)
        #expect(evidence.retailerKey == "demo-retailer")
        #expect(evidence.observedAt != nil)
        #expect(evidence.scope == "single synthetic store observation")
        #expect(evidence.limitations.contains("not current stock"))
        #expect(evidence.limitations.contains("nationwide availability"))
        #expect(evidence.source.name == "Halal Food EU synthetic retailer fixtures")
        #expect(evidence.source.kind == "synthetic")
        #expect(evidence.source.reference == "urn:halal-food-eu:synthetic-retailer:v1")
        #expect(evidence.source.license == "synthetic-fixture")
        #expect(
            evidence.source.attribution
                == "Synthetic Halal Food EU retailer evidence fixture. Not production source data."
        )
    }
}
