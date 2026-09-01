import Foundation
import Testing
@testable import HalalFoodEU

private final class ProductResultPresentationBundleToken: NSObject {}

@Suite("Offline product result presentation")
struct ProductResultPresentationTests {
    private let now = Date(timeIntervalSince1970: 1_788_220_800)

    @Test("Unresolved conflicts suppress a recorded positive status")
    func conflictSuppressesPositiveStatus() throws {
        let product = try makeProduct(
            status: .halalCertified,
            freshness: .current,
            verification: .humanVerified,
            conflictFlags: ["formulation-conflict"]
        )
        let warning = try #require(ProductResultPresentation.primaryWarning(for: product))

        #expect(warning.titleKey == "warning.conflict.title")
        #expect(warning.severity == .blocking)
        #expect(ProductResultPresentation.effectiveStatus(for: product) == .unknown)
    }

    @Test("Changed, stale, date-unknown, and unverified formulations suppress positive status")
    func unsafeEvidenceSuppressesPositiveStatus() throws {
        for freshness in [EvidenceFreshness.changedUnreviewed, .stale, .dateUnknown] {
            let product = try makeProduct(
                status: .halalReviewed,
                freshness: freshness,
                verification: .humanVerified
            )
            #expect(ProductResultPresentation.effectiveStatus(for: product) == .unknown)
            #expect(ProductResultPresentation.primaryWarning(for: product)?.severity == .blocking)
        }

        let unverified = try makeProduct(
            status: .halalCertified,
            freshness: .current,
            verification: .machineAssisted
        )
        #expect(ProductResultPresentation.effectiveStatus(for: unverified) == .unknown)
        #expect(ProductResultPresentation.primaryWarning(for: unverified)?.titleKey == "warning.unverified.title")
    }

    @Test("Blocking evidence never hides an existing not-halal result")
    func negativeStatusIsNotDowngraded() throws {
        let product = try makeProduct(
            status: .notHalal,
            freshness: .stale,
            verification: .humanVerified
        )
        #expect(ProductResultPresentation.primaryWarning(for: product)?.severity == .blocking)
        #expect(ProductResultPresentation.effectiveStatus(for: product) == .notHalal)
    }

    @Test("Conflict warning has precedence over formulation warnings")
    func conflictWarningPrecedesFreshness() throws {
        let product = try makeProduct(
            status: .halalCertified,
            freshness: .changedUnreviewed,
            verification: .unverified,
            conflictFlags: ["identity-conflict"]
        )
        #expect(ProductResultPresentation.primaryWarning(for: product)?.titleKey == "warning.conflict.title")
    }

    @Test("Questionable and not-halal reasons put unresolved evidence first")
    func reasonsAreEvidenceFirst() throws {
        let reasons = [
            reason(id: 1, severity: .positive),
            reason(id: 2, severity: .informational),
            reason(id: 3, severity: .caution),
            reason(id: 4, severity: .prohibitive),
        ]
        let product = try makeProduct(status: .questionable, reasons: reasons)
        #expect(ProductResultPresentation.orderedReasons(for: product).map(\.severity) == [
            .prohibitive, .caution, .informational, .positive,
        ])
    }

    @Test("Retailer wording stays qualified for every evidence type")
    func retailerWordingNeverClaimsStock() throws {
        let english = try localizedText(language: "en")
        let evidence = [
            retailer(.retailerFeedListing, observedAt: now, snapshotAt: now),
            retailer(.retailerObservation, observedAt: now),
            retailer(.communityStoreReport, observedAt: now),
        ]

        for item in evidence {
            let statement = ProductResultPresentation.retailerStatement(item, text: english)
                .lowercased()
            #expect(!statement.contains("currently in stock"))
            #expect(!statement.contains("available everywhere"))
            #expect(!statement.contains("normally sold at"))
        }
        #expect(ProductResultPresentation.retailerStatement(evidence[1], text: english).contains("Observed at a REWE store"))
        #expect(ProductResultPresentation.retailerStatement(evidence[2], text: english).contains("Community data reports REWE"))
    }

    @Test("English and German product-result resources preserve localized semantics")
    func localizedProductResultResources() throws {
        let english = try localizedText(language: "en")
        let german = try localizedText(language: "de")

        #expect(english.string("retailer.none") == "No retailer evidence in this catalog.")
        #expect(german.string("retailer.none") == "Keine Händlernachweise in diesem Katalog.")
        #expect(english.string("status.notHalal") == "Not halal")
        #expect(german.string("status.notHalal") == "Nicht halal")
        #expect(english.date(now).contains("2026"))
        #expect(german.date(now).contains("2026"))
    }

    @Test("Accessibility status text reports a former positive assessment as historical")
    func accessibilityDoesNotPresentFormerPositiveAsCurrent() throws {
        let english = try localizedText(language: "en")
        let product = try makeProduct(
            status: .halalCertified,
            freshness: .changedUnreviewed,
            verification: .humanVerified
        )
        let label = ProductResultPresentation.accessibilityAssessmentLabel(for: product, text: english)

        #expect(label.contains("Assessment: Needs review."))
        #expect(label.contains("Recorded earlier result: Halal certified"))
        #expect(label.contains("Recorded assessment summary:"))
    }

    @Test("Reviewed source attribution is preferred over a license-only fallback")
    func sourceAttributionUsesReviewedText() throws {
        let english = try localizedText(language: "en")
        let attributed = ProductSource(
            name: "Source",
            kind: "open-database",
            reference: "urn:test",
            license: "ODbL-1.0",
            retrievedAt: now,
            attribution: "Required attribution"
        )
        let fallback = ProductSource(
            name: "Source",
            kind: "open-database",
            reference: "urn:test",
            license: "ODbL-1.0",
            retrievedAt: now
        )
        #expect(ProductResultPresentation.sourceAttribution(attributed, text: english) == "Required attribution")
        #expect(ProductResultPresentation.sourceAttribution(fallback, text: english).contains("ODbL-1.0"))
    }

    private func localizedText(language: String) throws -> ProductResultText {
        let main = Bundle.main
        let path = try #require(main.path(forResource: language, ofType: "lproj"))
        let bundle = try #require(Bundle(path: path))
        return ProductResultText(bundle: bundle, locale: Locale(identifier: language == "de" ? "de_DE" : "en_DE"))
    }

    private func makeProduct(
        status: HalalStatus,
        freshness: EvidenceFreshness = .current,
        verification: EvidenceVerificationState = .humanVerified,
        conflictFlags: [String] = [],
        reasons: [AssessmentReason]? = nil
    ) throws -> ProductRecord {
        let source = ProductSource(
            name: "Synthetic source",
            kind: "synthetic",
            reference: "urn:test",
            license: "test-license",
            retrievedAt: now,
            attribution: "Test attribution"
        )
        let observation = IngredientObservation(
            text: "Water, oats.",
            languageCode: "en",
            observedAt: now.addingTimeInterval(-86_400),
            contentHash: String(repeating: "a", count: 64),
            freshness: freshness,
            source: source,
            details: IngredientObservationDetails(
                allergensText: "Oats.",
                tracesText: nil,
                retrievedAt: now,
                verificationState: verification
            )
        )
        let defaultReasons: [AssessmentReason]
        if let reasons {
            defaultReasons = reasons
        } else if status == .notHalal {
            defaultReasons = [reason(id: 1, severity: .prohibitive)]
        } else {
            defaultReasons = [reason(id: 1, severity: .informational)]
        }
        return ProductRecord(
            barcode: try Barcode(validating: "0200000000004"),
            name: "Test product",
            brand: "Test brand",
            observation: observation,
            assessment: HalalAssessment(
                status: status,
                summary: "Recorded assessment summary",
                methodologyVersion: "test-methodology",
                reviewedAt: now,
                reasons: defaultReasons,
                certifications: status == .halalCertified ? [
                    CertificationEvidence(
                        id: 1,
                        certifyingBody: "Test certifier",
                        certificateReference: "TEST-1",
                        scope: "Exact product",
                        validFrom: now.addingTimeInterval(-86_400),
                        validUntil: now.addingTimeInterval(86_400),
                        source: source,
                        scheme: "test-scheme",
                        lastCheckedAt: now
                    )
                ] : [],
                assessedAt: now,
                recheckAt: now.addingTimeInterval(86_400),
                approvedReviewerCount: 1
            ),
            catalogVersion: "test-catalog",
            details: ProductRecordDetails(
                market: "DE",
                brandOwner: nil,
                quantity: "1 L",
                conflictFlags: conflictFlags,
                retailerEvidence: [],
                remoteImages: []
            )
        )
    }

    private func retailer(
        _ kind: RetailerEvidenceKind,
        observedAt: Date? = nil,
        snapshotAt: Date? = nil
    ) -> RetailerEvidence {
        RetailerEvidence(
            id: retailerID(kind),
            kind: kind,
            retailerKey: "rewe",
            observedAt: observedAt,
            snapshotAt: snapshotAt,
            scope: "single observation",
            locationID: nil,
            limitations: "No current stock claim.",
            source: ProductSource(
                name: "Retailer source",
                kind: "community-observation",
                reference: "urn:retailer",
                license: "test-license",
                retrievedAt: now,
                attribution: "Retailer attribution"
            )
        )
    }

    private func retailerID(_ kind: RetailerEvidenceKind) -> Int64 {
        switch kind {
        case .retailerFeedListing: 1
        case .retailerObservation: 2
        case .communityStoreReport: 3
        }
    }

    private func reason(id: Int64, severity: EvidenceSeverity) -> AssessmentReason {
        AssessmentReason(
            id: id,
            code: "TEST-\(id)",
            title: "Reason \(id)",
            detail: "Reason detail \(id)",
            ingredient: nil,
            severity: severity
        )
    }
}
