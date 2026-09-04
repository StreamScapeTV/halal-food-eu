import Foundation
import Testing
@testable import HalalFoodEU

@Suite("Saved product marker validation")
struct SavedProductVersionMarkerValidationTests {
    @Test("A valid generated marker round-trips through the persisted JSON contract")
    func validMarkerRoundTrips() throws {
        let marker = SavedProductVersionMarker(product: nil)
        let encoded = try JSONEncoder().encode(marker)
        let decoded = try JSONDecoder().decode(SavedProductVersionMarker.self, from: encoded)
        #expect(decoded == marker)
    }

    @Test("A present saved product rejects a malformed persisted fingerprint")
    func malformedPresentFingerprintFailsClosed() throws {
        let json = #"{"fingerprintSchemaVersion":1,"wasPresent":true,"recordFingerprint":"not-a-sha256"}"#
        #expect(throws: DecodingError.self) {
            _ = try JSONDecoder().decode(
                SavedProductVersionMarker.self,
                from: Data(json.utf8)
            )
        }
    }

    @Test("A previously missing product cannot carry a stale record fingerprint")
    func missingProductFingerprintFailsClosed() throws {
        let fingerprint = String(repeating: "a", count: 64)
        let json = "{\"fingerprintSchemaVersion\":1,\"wasPresent\":false,\"recordFingerprint\":\"\(fingerprint)\"}"
        #expect(throws: DecodingError.self) {
            _ = try JSONDecoder().decode(
                SavedProductVersionMarker.self,
                from: Data(json.utf8)
            )
        }
    }

    @Test("Unknown fingerprint schemas fail closed instead of guessing comparison semantics")
    func unknownFingerprintSchemaFailsClosed() throws {
        let json = #"{"fingerprintSchemaVersion":99,"wasPresent":false,"recordFingerprint":null}"#
        #expect(throws: DecodingError.self) {
            _ = try JSONDecoder().decode(
                SavedProductVersionMarker.self,
                from: Data(json.utf8)
            )
        }
    }

    @Test("Refreshing identical source bytes does not claim that the product changed")
    func retrievalTimestampOnlyChangeIsIgnored() throws {
        let barcode = try Barcode(validating: "0200000000004")
        let original = makeObservedProduct(
            barcode: barcode,
            sourceRetrievedAt: Date(timeIntervalSince1970: 1_700_000_000),
            observationRetrievedAt: Date(timeIntervalSince1970: 1_700_000_100)
        )
        let refreshed = makeObservedProduct(
            barcode: barcode,
            sourceRetrievedAt: Date(timeIntervalSince1970: 1_800_000_000),
            observationRetrievedAt: Date(timeIntervalSince1970: 1_800_000_100)
        )

        let marker = SavedProductVersionMarker(product: original)
        #expect(marker.comparison(with: refreshed) == .unchanged)
    }

    private func makeObservedProduct(
        barcode: Barcode,
        sourceRetrievedAt: Date,
        observationRetrievedAt: Date
    ) -> ProductRecord {
        let source = ProductSource(
            name: "Fixture Source",
            kind: "community-database",
            reference: "https://example.com/product/0200000000004",
            license: "Fixture License",
            retrievedAt: sourceRetrievedAt,
            attribution: "Fixture attribution"
        )
        let observation = IngredientObservation(
            text: "water, oats",
            languageCode: "en",
            observedAt: Date(timeIntervalSince1970: 1_690_000_000),
            contentHash: String(repeating: "b", count: 64),
            freshness: .current,
            source: source,
            details: IngredientObservationDetails(
                allergensText: "oats",
                tracesText: nil,
                retrievedAt: observationRetrievedAt,
                verificationState: .humanVerified
            )
        )
        return ProductRecord(
            barcode: barcode,
            name: "Fixture Oat Drink",
            brand: "Fixture Brand",
            observation: observation,
            assessment: .unreviewedUnknown,
            catalogVersion: "fixture-v1",
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
