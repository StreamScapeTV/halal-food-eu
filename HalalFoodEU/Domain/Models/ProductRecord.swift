import Foundation

struct ProductSource: Codable, Equatable, Sendable {
    let name: String
    let kind: String
    let reference: String
    let license: String
    let retrievedAt: Date
    let attribution: String?

    init(
        name: String,
        kind: String,
        reference: String,
        license: String,
        retrievedAt: Date,
        attribution: String? = nil
    ) {
        self.name = name
        self.kind = kind
        self.reference = reference
        self.license = license
        self.retrievedAt = retrievedAt
        self.attribution = attribution
    }
}

struct IngredientObservationDetails: Codable, Equatable, Sendable {
    let allergensText: String?
    let tracesText: String?
    let retrievedAt: Date
    let verificationState: EvidenceVerificationState
}

struct IngredientObservation: Codable, Equatable, Sendable {
    let text: String
    let languageCode: String
    let observedAt: Date?
    let contentHash: String
    let freshness: EvidenceFreshness
    let source: ProductSource
    let details: IngredientObservationDetails?

    init(
        text: String,
        languageCode: String,
        observedAt: Date?,
        contentHash: String,
        freshness: EvidenceFreshness,
        source: ProductSource,
        details: IngredientObservationDetails? = nil
    ) {
        self.text = text
        self.languageCode = languageCode
        self.observedAt = observedAt
        self.contentHash = contentHash
        self.freshness = freshness
        self.source = source
        self.details = details
    }
}

struct RetailerEvidence: Codable, Equatable, Sendable, Identifiable {
    let id: Int64
    let kind: RetailerEvidenceKind
    let retailerKey: String
    let observedAt: Date?
    let snapshotAt: Date?
    let scope: String?
    let locationID: String?
    let limitations: String
    let source: ProductSource
}

struct RemoteProductImage: Codable, Equatable, Sendable, Identifiable {
    let id: Int64
    let purpose: RemoteImagePurpose
    let url: URL
    let imageID: String
    let revision: String?
    let source: ProductSource
}

struct ProductRecordDetails: Codable, Equatable, Sendable {
    let market: String
    let brandOwner: String?
    let quantity: String?
    let conflictFlags: [String]
    let retailerEvidence: [RetailerEvidence]
    let remoteImages: [RemoteProductImage]
}

struct ProductRecord: Codable, Equatable, Sendable {
    let barcode: Barcode
    let name: String
    let brand: String?
    let observation: IngredientObservation?
    let assessment: HalalAssessment
    let catalogVersion: String
    let details: ProductRecordDetails?

    init(
        barcode: Barcode,
        name: String,
        brand: String?,
        observation: IngredientObservation?,
        assessment: HalalAssessment,
        catalogVersion: String,
        details: ProductRecordDetails? = nil
    ) {
        self.barcode = barcode
        self.name = name
        self.brand = brand
        self.observation = observation
        self.assessment = assessment
        self.catalogVersion = catalogVersion
        self.details = details
    }
}
