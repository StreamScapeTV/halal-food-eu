import Foundation
import Testing
@testable import HalalFoodEU

private final class ProductDetailProjectionBundleToken: NSObject {}

@Suite("Production SQLite product-detail projection")
struct ProductDetailProjectionTests {
    @Test("Certified product carries exact identity, ingredient, and certification lineage")
    func certifiedProductProjection() async throws {
        let catalog = try makeCatalog()
        let product = try #require(
            try await catalog.product(for: Barcode(validating: "0200000000004"))
        )

        #expect(product.barcode.rawValue == "00200000000004")
        #expect(product.details?.market == "DE")
        #expect(product.details?.quantity == "1 L")
        #expect(product.details?.brandOwner == "StreamScapeTV Demo")
        #expect(product.details?.conflictFlags.isEmpty == true)
        #expect(product.observation?.text == "Water, oats (12%), sunflower oil, sea salt.")
        #expect(product.observation?.observedAt != nil)
        #expect(product.observation?.details?.retrievedAt != nil)
        #expect(product.observation?.details?.verificationState == .humanVerified)
        #expect(product.observation?.source.attribution?.isEmpty == false)
        #expect(product.assessment.status == .halalCertified)
        #expect(product.assessment.assessedAt != nil)
        #expect(product.assessment.reviewedAt != nil)
        #expect(product.assessment.approvedReviewerCount == 1)
        let certification = try #require(product.assessment.certifications.first)
        #expect(certification.scheme == "synthetic-demo-scheme")
        #expect(certification.scope == "Synthetic demonstration product only")
        #expect(certification.lastCheckedAt != nil)
        #expect(certification.source.attribution?.isEmpty == false)
    }

    @Test("Retailer observation remains dated, limited, and distinct from stock claims")
    func retailerProjection() async throws {
        let catalog = try makeCatalog()
        let product = try #require(
            try await catalog.product(for: Barcode(validating: "0200000000028"))
        )
        let retailer = try #require(product.details?.retailerEvidence.first)

        #expect(retailer.kind == .retailerObservation)
        #expect(retailer.retailerKey == "demo-retailer")
        #expect(retailer.observedAt != nil)
        #expect(retailer.scope == "single synthetic store observation")
        #expect(retailer.locationID == "demo-store-koblenz")
        #expect(retailer.limitations == "Dated store observation only; not current stock or nationwide availability.")
        #expect(retailer.source.attribution?.isEmpty == false)
    }

    @Test("Remote image references remain inert HTTPS metadata in the offline projection")
    func remoteImageProjection() async throws {
        let catalog = try makeCatalog()
        let product = try #require(
            try await catalog.product(for: Barcode(validating: "0200000000028"))
        )
        let image = try #require(product.details?.remoteImages.first)

        #expect(image.purpose == .front)
        #expect(image.url.scheme == "https")
        #expect(image.imageID == "demo-dessert-front-v1")
        #expect(image.revision == "v1")
    }

    private func makeCatalog() throws -> SQLiteProductCatalog {
        let bundle = Bundle(for: ProductDetailProjectionBundleToken.self)
        let databaseURL = try #require(
            bundle.url(forResource: "catalog", withExtension: "sqlite3")
        )
        let manifestURL = try #require(
            bundle.url(forResource: "catalog-manifest", withExtension: "json")
        )
        return SQLiteProductCatalog(databaseURL: databaseURL, manifestURL: manifestURL)
    }
}
