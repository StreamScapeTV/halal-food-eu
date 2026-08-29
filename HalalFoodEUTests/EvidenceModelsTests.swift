import Foundation
import Testing
@testable import HalalFoodEU

@Suite("Immutable evidence envelope")
struct EvidenceModelsTests {
    @Test("Decodes the committed schema-v1 evidence fixture")
    func decodesCommittedFixture() throws {
        let bundle = Bundle(for: EvidenceFixtureBundleToken.self)
        let url = try #require(
            bundle.url(forResource: "sample-evidence-v1", withExtension: "json"),
            "sample-evidence-v1.json must be copied into the unit-test bundle"
        )

        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        let envelope = try decoder.decode(
            EvidenceEnvelopeV1.self,
            from: Data(contentsOf: url)
        )

        #expect(envelope.schemaVersion == 1)
        #expect(envelope.currentSelections.count == 2)
        #expect(envelope.ingredients.count == 3)
        #expect(envelope.remoteImages.count == 1)
        #expect(envelope.packageEvidence.count == 1)
        #expect(envelope.releases.count == 1)

        let dessertIngredients = try #require(
            envelope.ingredients.first {
                $0.gtin == "00200000000028" && $0.sourceRevision == "formula-v2"
            }
        )
        #expect(dessertIngredients.supersedesID != nil)
        #expect(dessertIngredients.verificationState == .humanVerified)

        let image = try #require(envelope.remoteImages.first)
        #expect(image.url.scheme == "https")

        let oatAssessment = try #require(
            envelope.assessments.first { $0.gtin == "00200000000004" }
        )
        #expect(oatAssessment.status == .halalCertified)
        #expect(oatAssessment.certificationIDs.count == 1)
    }

    @Test("Evidence enums round-trip without UI or SQLite coupling")
    func enumsRoundTrip() throws {
        struct Probe: Codable, Equatable {
            let retailer: RetailerEvidenceKind
            let review: EvidenceReviewState
            let capture: IngredientCaptureMethod
            let image: RemoteImagePurpose
            let validity: AssessmentValidityKind
        }

        let value = Probe(
            retailer: .retailerObservation,
            review: .approved,
            capture: .sourceText,
            image: .ingredients,
            validity: .invalidated
        )
        let encoded = try JSONEncoder().encode(value)
        let decoded = try JSONDecoder().decode(Probe.self, from: encoded)
        #expect(decoded == value)
    }

    @Test("Market identity remains explicit and value-semantic")
    func marketIsPartOfEvidenceIdentity() {
        let germany = ProductIdentityEvidence(
            id: "hfeu:identity:sha256:" + String(repeating: "a", count: 64),
            gtin: "00200000000028",
            originalBarcode: "0200000000028",
            market: "DE",
            sourceKey: "synthetic-core",
            sourceRecordID: "demo",
            sourceRevision: "v1",
            name: "Demo",
            brandOwner: nil,
            brand: nil,
            quantity: nil,
            categories: nil,
            packaging: nil,
            observedAt: nil,
            retrievedAt: Date(timeIntervalSince1970: 0),
            sourceModifiedAt: nil,
            confidence: .high
        )
        let france = ProductIdentityEvidence(
            id: germany.id,
            gtin: germany.gtin,
            originalBarcode: germany.originalBarcode,
            market: "FR",
            sourceKey: germany.sourceKey,
            sourceRecordID: germany.sourceRecordID,
            sourceRevision: germany.sourceRevision,
            name: germany.name,
            brandOwner: germany.brandOwner,
            brand: germany.brand,
            quantity: germany.quantity,
            categories: germany.categories,
            packaging: germany.packaging,
            observedAt: germany.observedAt,
            retrievedAt: germany.retrievedAt,
            sourceModifiedAt: germany.sourceModifiedAt,
            confidence: germany.confidence
        )
        #expect(germany != france)
    }
}

private final class EvidenceFixtureBundleToken {}
