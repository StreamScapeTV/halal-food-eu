import Foundation
import Testing
@testable import HalalFoodEU

@Suite("German product-result localization regressions")
struct ProductResultGermanLocalizationRegressionTests {
    @Test("German retailer observation keeps date and retailer in grammatical order")
    func retailerObservationPlaceholderOrder() throws {
        let main = Bundle.main
        let path = try #require(main.path(forResource: "de", ofType: "lproj"))
        let bundle = try #require(Bundle(path: path))
        let text = ProductResultText(bundle: bundle, locale: Locale(identifier: "de_DE"))
        let observedAt = Date(timeIntervalSince1970: 1_788_220_800)
        let evidence = RetailerEvidence(
            id: 1,
            kind: .retailerObservation,
            retailerKey: "rewe",
            observedAt: observedAt,
            snapshotAt: nil,
            scope: "single observation",
            locationID: nil,
            limitations: "No current stock claim.",
            source: ProductSource(
                name: "Retailer source",
                kind: "community-observation",
                reference: "urn:retailer",
                license: "test-license",
                retrievedAt: observedAt,
                attribution: "Retailer attribution"
            )
        )

        let statement = ProductResultPresentation.retailerStatement(evidence, text: text)
        #expect(statement == "Am \(text.date(observedAt)) in einer REWE-Filiale beobachtet")
    }
}
