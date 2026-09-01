import CryptoKit
import Foundation
import Testing
@testable import HalalFoodEU

private final class ProductEvidenceSubmissionBundleToken: NSObject {}

@Suite("Backend-free product evidence submission")
struct ProductEvidenceSubmissionTests {
    private let now = Date(timeIntervalSince1970: 1_788_220_800) // 2026-09-01T00:00:00Z
    private let submissionID = "hfeu-submission-12345678-1234-1234-1234-123456789abc"

    @Test("Missing-product package is machine-readable and uses the stable subject")
    func preparesMissingProductPackage() throws {
        let barcode = try Barcode(validating: "0200000000004")
        let request = ProductEvidenceSubmissionRequest.missingProduct(
            barcode: barcode,
            catalogVersion: "2026.09.0"
        )
        var draft = acceptedDraft(request: request)
        draft.productName = "Test product"
        draft.brand = "Test brand"
        draft.retailer = "Example retailer"
        let isolatedSubmissionID = packageSubmissionID(1)

        let builder = try makeBuilder()
        let package = try builder(
            submissionID: isolatedSubmissionID,
            request: request,
            draft: draft,
            attachments: requiredAttachments(for: .missingProduct),
            now: now
        )
        defer { package.cleanup() }

        #expect(package.destinationEmail == "info@faruqi.dev")
        #expect(package.subject == "[Halal Food EU Product] \(isolatedSubmissionID) \(barcode.rawValue)")
        #expect(package.envelope.schemaVersion == 1)
        #expect(package.envelope.sourceType == "user-package-evidence")
        #expect(package.envelope.gtin == "00200000000004")
        #expect(package.envelope.issueType == .missingProduct)
        #expect(package.envelope.market == "DE")
        #expect(package.envelope.attachments.map(\.purpose) == [.barcode, .front, .ingredients])
        #expect(package.mailAttachments.first?.fileName == "product-evidence-submission.json")
        #expect(package.shareItems.count == 4)
        #expect(package.body.contains("--- BEGIN HALAL FOOD EU PRODUCT EVIDENCE JSON ---"))
        #expect(FileManager.default.fileExists(atPath: package.temporaryDirectory.path))
    }

    @Test("Consent and future evidence are fail-closed")
    func rejectsInvalidConsentAndFutureDate() throws {
        let barcode = try Barcode(validating: "0200000000004")
        let request = ProductEvidenceSubmissionRequest.missingProduct(
            barcode: barcode,
            catalogVersion: "2026.09.0"
        )
        let builder = try makeBuilder()

        var missingConsent = ProductEvidenceSubmissionDraft(request: request, observedAt: now)
        #expect(throws: ProductEvidenceSubmissionError.consentRequired) {
            try builder(
                submissionID: submissionID,
                request: request,
                draft: missingConsent,
                attachments: requiredAttachments(for: .missingProduct),
                now: now
            )
        }

        missingConsent = acceptedDraft(request: request)
        missingConsent.observedAt = now.addingTimeInterval(60)
        #expect(throws: ProductEvidenceSubmissionError.futureEvidenceDate) {
            try builder(
                submissionID: submissionID,
                request: request,
                draft: missingConsent,
                attachments: requiredAttachments(for: .missingProduct),
                now: now
            )
        }
    }

    @Test("Missing-product drafts cannot switch to a correction type")
    func rejectsIssueTypeBoundaryChange() throws {
        let request = ProductEvidenceSubmissionRequest.missingProduct(
            barcode: try Barcode(validating: "0200000000004"),
            catalogVersion: "2026.09.0"
        )
        var draft = acceptedDraft(request: request)
        draft.issueType = .identityCorrection
        #expect(throws: ProductEvidenceSubmissionError.invalidIssueType) {
            try makeBuilder()(
                submissionID: submissionID,
                request: request,
                draft: draft,
                attachments: requiredAttachments(for: .identityCorrection),
                now: now
            )
        }
    }

    @Test("Attachment hashes are revalidated before composer presentation")
    func rejectsTamperedAttachmentHash() throws {
        let request = ProductEvidenceSubmissionRequest.missingProduct(
            barcode: try Barcode(validating: "0200000000004"),
            catalogVersion: "2026.09.0"
        )
        var attachments = requiredAttachments(for: .missingProduct)
        let original = attachments.removeFirst()
        attachments.insert(
            SanitizedProductEvidenceAttachment(
                id: original.id,
                purpose: original.purpose,
                data: original.data,
                pixelWidth: original.pixelWidth,
                pixelHeight: original.pixelHeight,
                sha256: String(repeating: "0", count: 64)
            ),
            at: 0
        )
        #expect(throws: ProductEvidenceSubmissionError.invalidAttachment) {
            try makeBuilder()(
                submissionID: submissionID,
                request: request,
                draft: acceptedDraft(request: request),
                attachments: attachments,
                now: now
            )
        }
    }

    @Test("Issue-specific package photos are required")
    func requiresPurposeSpecificPhotos() throws {
        let barcode = try Barcode(validating: "0200000000004")
        let request = ProductEvidenceSubmissionRequest.missingProduct(
            barcode: barcode,
            catalogVersion: "2026.09.0"
        )
        let draft = acceptedDraft(request: request)
        let builder = try makeBuilder()
        let withoutIngredients = [attachment(.barcode, byte: 1), attachment(.front, byte: 2)]

        #expect(throws: ProductEvidenceSubmissionError.missingRequiredPhoto(.ingredients)) {
            try builder(
                submissionID: submissionID,
                request: request,
                draft: draft,
                attachments: withoutIngredients,
                now: now
            )
        }
    }

    @Test("Correction carries challenged evidence but never serializes the halal assessment")
    func correctionDoesNotLeakAcceptedAssessment() throws {
        let barcode = try Barcode(validating: "0200000000004")
        let product = ProductRecord(
            barcode: barcode,
            name: "Reviewed product",
            brand: "Brand",
            observation: IngredientObservation(
                text: "water, oats",
                languageCode: "en",
                observedAt: now.addingTimeInterval(-86_400),
                contentHash: String(repeating: "a", count: 64),
                freshness: .current,
                source: ProductSource(
                    name: "Open Food Facts",
                    kind: "open-database",
                    reference: "https://example.invalid/product",
                    license: "ODbL-1.0",
                    retrievedAt: now.addingTimeInterval(-3_600)
                )
            ),
            assessment: HalalAssessment(
                status: .halalReviewed,
                summary: "Reviewed outcome that must not be submitted as accepted evidence.",
                methodologyVersion: "methodology-v1",
                reviewedAt: now.addingTimeInterval(-1_800),
                reasons: [
                    AssessmentReason(
                        id: 1,
                        code: "TEST",
                        title: "test",
                        detail: "test",
                        ingredient: nil,
                        severity: .positive
                    )
                ],
                certifications: []
            ),
            catalogVersion: "2026.09.0"
        )
        let request = ProductEvidenceSubmissionRequest.correction(
            product: product,
            issueType: .ingredientsCorrection
        )
        let draft = acceptedDraft(request: request)
        let builder = try makeBuilder()
        let package = try builder(
            submissionID: packageSubmissionID(2),
            request: request,
            draft: draft,
            attachments: requiredAttachments(for: .ingredientsCorrection),
            now: now
        )
        defer { package.cleanup() }

        let json = try #require(package.mailAttachments.first)
        let text = try #require(String(data: json.data, encoding: .utf8))
        #expect(text.contains("Open Food Facts"))
        #expect(text.contains(String(repeating: "a", count: 64)))
        #expect(!text.contains("halal-reviewed"))
        #expect(!text.contains("Reviewed outcome that must not be submitted"))
        #expect(!text.contains("methodology-v1"))
    }

    @Test("Temporary package is deleted explicitly")
    func cleanupDeletesTemporaryFiles() throws {
        let request = ProductEvidenceSubmissionRequest.missingProduct(
            barcode: try Barcode(validating: "0200000000004"),
            catalogVersion: "2026.09.0"
        )
        let package = try makeBuilder()(
            submissionID: packageSubmissionID(3),
            request: request,
            draft: acceptedDraft(request: request),
            attachments: requiredAttachments(for: .missingProduct),
            now: now
        )
        let path = package.temporaryDirectory.path
        #expect(FileManager.default.fileExists(atPath: path))
        package.cleanup()
        #expect(!FileManager.default.fileExists(atPath: path))
    }

    @Test("Submission schema is closed and cannot carry an accepted halal result")
    func schemaExcludesHalalVerdict() throws {
        let bundle = Bundle(for: ProductEvidenceSubmissionBundleToken.self)
        let url = try #require(bundle.url(forResource: "product-evidence-submission-v1.schema", withExtension: "json"))
        let root = try #require(
            JSONSerialization.jsonObject(with: Data(contentsOf: url)) as? [String: Any]
        )
        #expect((root["additionalProperties"] as? Bool) == false)
        let properties = try #require(root["properties"] as? [String: Any])
        #expect(properties["halalStatus"] == nil)
        #expect(properties["assessment"] == nil)
        #expect(properties["senderEmail"] == nil)
        #expect(properties["sourceType"] != nil)
        #expect(properties["attachments"] != nil)
        #expect(properties["consent"] != nil)
    }

    @Test("Canonical public submission recipient loads without a secret")
    func loadsPublicSubmissionConfiguration() throws {
        let data = Data(#"{"schemaVersion":1,"publicValues":{"OPEN_FOOD_FACTS_CONTACT_EMAIL":"info@faruqi.dev","OPEN_FOOD_FACTS_USER_AGENT":"HalalFoodEU/0.1 (info@faruqi.dev)","PRODUCT_SUBMISSION_EMAIL":"info@faruqi.dev"}}"#.utf8)
        let config = try ProductEvidenceSubmissionConfigurationLoader.decodePublicConfiguration(data)
        #expect(config.destinationEmail == "info@faruqi.dev")

        let invalid = Data(#"{"schemaVersion":1,"publicValues":{"OPEN_FOOD_FACTS_CONTACT_EMAIL":"info@faruqi.dev","OPEN_FOOD_FACTS_USER_AGENT":"HalalFoodEU/0.1 (info@faruqi.dev)","PRODUCT_SUBMISSION_EMAIL":"not-an-email"}}"#.utf8)
        #expect(throws: ProductEvidenceSubmissionConfigurationLoadError.invalidPublicConfiguration) {
            try ProductEvidenceSubmissionConfigurationLoader.decodePublicConfiguration(invalid)
        }
    }

    private func makeBuilder() throws -> PrepareProductEvidenceSubmission {
        PrepareProductEvidenceSubmission(
            configuration: try ProductEvidenceSubmissionConfiguration(
                destinationEmail: "info@faruqi.dev"
            ),
            appVersion: "0.1.0",
            catalogVersion: "2026.09.0"
        )
    }

    private func packageSubmissionID(_ suffix: Int) -> String {
        "hfeu-submission-12345678-1234-1234-1234-123456789ab\(suffix)"
    }

    private func acceptedDraft(
        request: ProductEvidenceSubmissionRequest
    ) -> ProductEvidenceSubmissionDraft {
        var draft = ProductEvidenceSubmissionDraft(request: request, observedAt: now)
        draft.ownsOrMaySubmitPhotos = true
        draft.packageEvidenceOnly = true
        draft.projectMayReviewAndRedact = true
        draft.redistributionRequiresReview = true
        draft.noGuaranteedCatalogOrHalalOutcome = true
        draft.consentAcceptedAt = now
        return draft
    }

    private func requiredAttachments(
        for issueType: ProductEvidenceIssueType
    ) -> [SanitizedProductEvidenceAttachment] {
        let order: [ProductEvidencePhotoPurpose] = [.barcode, .front, .ingredients, .certification, .nutrition]
        return order.enumerated().compactMap { index, purpose in
            issueType.requiredPhotoPurposes.contains(purpose)
                ? attachment(purpose, byte: UInt8(index + 1))
                : nil
        }
    }

    private func attachment(
        _ purpose: ProductEvidencePhotoPurpose,
        byte: UInt8
    ) -> SanitizedProductEvidenceAttachment {
        let data = Data(repeating: byte, count: 1_024)
        let digest = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
        return SanitizedProductEvidenceAttachment(
            id: UUID(uuidString: "00000000-0000-0000-0000-00000000000\(byte)") ?? UUID(),
            purpose: purpose,
            data: data,
            pixelWidth: 1_200,
            pixelHeight: 800,
            sha256: digest
        )
    }
}

@MainActor
@Suite("Submission delivery routing")
struct ProductEvidenceDeliveryTests {
    @Test("Mail-unavailable state keeps share/copy as explicit fallbacks")
    func mailUnavailable() throws {
        let composer = StubProductEvidenceComposer(mail: .unavailable, share: .share)
        let configuration = ProductEvidenceSubmissionRuntimeConfiguration(
            submission: try ProductEvidenceSubmissionConfiguration(destinationEmail: "info@faruqi.dev"),
            appVersion: "0.1.0",
            catalogVersion: "2026.09.0"
        )
        let viewModel = ProductEvidenceSubmissionViewModel(
            request: .missingProduct(
                barcode: try Barcode(validating: "0200000000004"),
                catalogVersion: "2026.09.0"
            ),
            configuration: configuration,
            composer: composer,
            submissionUUID: UUID(uuidString: "12345678-1234-1234-1234-123456789ABC")!,
            now: Date(timeIntervalSince1970: 1_788_220_800)
        )

        viewModel.prepareEmail()
        #expect(viewModel.deliveryPresentation == nil)
        #expect(viewModel.statusMessage?.contains("Mail is not configured") == true)
        #expect(composer.route(for: .share) == .share)
    }
}

@MainActor
private final class StubProductEvidenceComposer: ProductEvidenceComposer {
    let mail: ProductEvidenceDeliveryRoute
    let share: ProductEvidenceDeliveryRoute

    init(mail: ProductEvidenceDeliveryRoute, share: ProductEvidenceDeliveryRoute) {
        self.mail = mail
        self.share = share
    }

    func route(for preference: ProductEvidenceDeliveryPreference) -> ProductEvidenceDeliveryRoute {
        switch preference {
        case .email: mail
        case .share: share
        }
    }
}
