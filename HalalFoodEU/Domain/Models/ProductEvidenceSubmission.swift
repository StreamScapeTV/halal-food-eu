import Foundation

enum ProductEvidenceIssueType: String, Codable, CaseIterable, Sendable, Identifiable {
    case missingProduct = "missing-product"
    case ingredientsCorrection = "ingredients-correction"
    case identityCorrection = "identity-correction"
    case statusCertificationCorrection = "status-certification-correction"

    var id: String { rawValue }

    var requiredPhotoPurposes: Set<ProductEvidencePhotoPurpose> {
        switch self {
        case .missingProduct:
            [.barcode, .front, .ingredients]
        case .ingredientsCorrection:
            [.barcode, .ingredients]
        case .identityCorrection:
            [.barcode, .front]
        case .statusCertificationCorrection:
            [.barcode, .front]
        }
    }
}

enum ProductEvidencePhotoPurpose: String, Codable, CaseIterable, Sendable, Identifiable {
    case barcode
    case front
    case ingredients
    case certification
    case nutrition

    var id: String { rawValue }
}

struct ProductEvidenceProductContext: Codable, Equatable, Sendable {
    let name: String?
    let brand: String?
    let quantity: String?
}

struct ProductEvidenceRetailerContext: Codable, Equatable, Sendable {
    let retailer: String?
    let city: String?
    let store: String?
}

struct ProductEvidenceCatalogContext: Codable, Equatable, Sendable {
    let catalogVersion: String
    let sourceName: String?
    let sourceKind: String?
    let sourceReference: String?
    let observedAt: Date?
    let retrievedAt: Date?
    let contentHash: String?
}

struct ProductEvidenceAttachmentManifestEntry: Codable, Equatable, Sendable {
    let fileName: String
    let purpose: ProductEvidencePhotoPurpose
    let mimeType: String
    let pixelWidth: Int
    let pixelHeight: Int
    let byteSize: Int
    let sha256: String
    let ownershipState: String
    let privacyState: String
    let metadataState: String
}

struct ProductEvidenceConsent: Codable, Equatable, Sendable {
    let version: String
    let acceptedAt: Date
    let ownsOrMaySubmitPhotos: Bool
    let packageEvidenceOnly: Bool
    let projectMayReviewAndRedact: Bool
    let redistributionRequiresReview: Bool
    let noGuaranteedCatalogOrHalalOutcome: Bool
}

struct ProductEvidenceSubmissionEnvelope: Codable, Equatable, Sendable {
    let schemaVersion: Int
    let sourceType: String
    let submissionID: String
    let appVersion: String
    let catalogVersion: String
    let gtin: String
    let issueType: ProductEvidenceIssueType
    let market: String
    let product: ProductEvidenceProductContext
    let retailer: ProductEvidenceRetailerContext
    let observedAt: Date
    let currentCatalogEvidence: ProductEvidenceCatalogContext?
    let attachments: [ProductEvidenceAttachmentManifestEntry]
    let consent: ProductEvidenceConsent
    let notes: String?
}

struct SanitizedProductEvidenceAttachment: Identifiable, Equatable, Sendable {
    let id: UUID
    let purpose: ProductEvidencePhotoPurpose
    let data: Data
    let pixelWidth: Int
    let pixelHeight: Int
    let sha256: String

    init(
        id: UUID = UUID(),
        purpose: ProductEvidencePhotoPurpose,
        data: Data,
        pixelWidth: Int,
        pixelHeight: Int,
        sha256: String
    ) {
        self.id = id
        self.purpose = purpose
        self.data = data
        self.pixelWidth = pixelWidth
        self.pixelHeight = pixelHeight
        self.sha256 = sha256
    }
}

struct ProductEvidenceSubmissionRequest: Equatable, Sendable {
    let barcode: Barcode
    let issueType: ProductEvidenceIssueType
    let market: String
    let productName: String?
    let brand: String?
    let quantity: String?
    let catalogVersion: String
    let currentCatalogEvidence: ProductEvidenceCatalogContext?

    static func missingProduct(
        barcode: Barcode,
        catalogVersion: String,
        market: String = "DE"
    ) -> ProductEvidenceSubmissionRequest {
        ProductEvidenceSubmissionRequest(
            barcode: barcode,
            issueType: .missingProduct,
            market: market,
            productName: nil,
            brand: nil,
            quantity: nil,
            catalogVersion: catalogVersion,
            currentCatalogEvidence: nil
        )
    }

    static func correction(
        product: ProductRecord,
        issueType: ProductEvidenceIssueType,
        market: String = "DE"
    ) -> ProductEvidenceSubmissionRequest {
        precondition(issueType != .missingProduct)
        let context = product.observation.map { observation in
            ProductEvidenceCatalogContext(
                catalogVersion: product.catalogVersion,
                sourceName: observation.source.name,
                sourceKind: observation.source.kind,
                sourceReference: observation.source.reference,
                observedAt: observation.observedAt,
                retrievedAt: observation.source.retrievedAt,
                contentHash: observation.contentHash
            )
        }
        return ProductEvidenceSubmissionRequest(
            barcode: product.barcode,
            issueType: issueType,
            market: market,
            productName: product.name,
            brand: product.brand,
            quantity: nil,
            catalogVersion: product.catalogVersion,
            currentCatalogEvidence: context
        )
    }
}

struct ProductEvidenceSubmissionDraft: Equatable, Sendable {
    var issueType: ProductEvidenceIssueType
    var observedAt: Date
    var productName: String
    var brand: String
    var quantity: String
    var retailer: String
    var city: String
    var store: String
    var notes: String
    var ownsOrMaySubmitPhotos: Bool
    var packageEvidenceOnly: Bool
    var projectMayReviewAndRedact: Bool
    var redistributionRequiresReview: Bool
    var noGuaranteedCatalogOrHalalOutcome: Bool
    var consentAcceptedAt: Date?

    init(request: ProductEvidenceSubmissionRequest, observedAt: Date) {
        issueType = request.issueType
        self.observedAt = observedAt
        productName = request.productName ?? ""
        brand = request.brand ?? ""
        quantity = request.quantity ?? ""
        retailer = ""
        city = ""
        store = ""
        notes = ""
        ownsOrMaySubmitPhotos = false
        packageEvidenceOnly = false
        projectMayReviewAndRedact = false
        redistributionRequiresReview = false
        noGuaranteedCatalogOrHalalOutcome = false
        consentAcceptedAt = nil
    }
}

struct ProductEvidenceMailAttachment: Equatable, Sendable {
    let fileName: String
    let mimeType: String
    let data: Data
}

struct PreparedProductEvidenceSubmission: Sendable {
    let envelope: ProductEvidenceSubmissionEnvelope
    let destinationEmail: String
    let subject: String
    let body: String
    let mailAttachments: [ProductEvidenceMailAttachment]
    let shareItems: [URL]
    let temporaryDirectory: URL

    func cleanup(fileManager: FileManager = .default) {
        try? fileManager.removeItem(at: temporaryDirectory)
    }
}
