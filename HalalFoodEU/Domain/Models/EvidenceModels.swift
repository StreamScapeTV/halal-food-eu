import Foundation

enum EvidenceSourceClass: String, Codable, CaseIterable, Sendable {
    case packagePhoto = "package-photo"
    case manufacturer
    case certifier
    case retailerOfficial = "retailer-official"
    case openDatabase = "open-database"
    case communityObservation = "community-observation"
    case identityRegistry = "identity-registry"
    case synthetic
}

enum EvidenceSourceAccessMethod: String, Codable, CaseIterable, Sendable {
    case package
    case publicBulk = "public-bulk"
    case publicAPI = "public-api"
    case partnerAPI = "partner-api"
    case sftp
    case objectFeed = "object-feed"
    case manual
    case synthetic
}

enum EvidenceIdentityConfidence: String, Codable, CaseIterable, Sendable {
    case high
    case medium
    case low
    case conflict
}

enum IngredientCaptureMethod: String, Codable, CaseIterable, Sendable {
    case sourceText = "source-text"
    case packageTranscription = "package-transcription"
    case ocr
    case manualReview = "manual-review"
}

enum EvidenceVerificationState: String, Codable, CaseIterable, Sendable {
    case unverified
    case machineAssisted = "machine-assisted"
    case humanVerified = "human-verified"
}

enum RetailerEvidenceKind: String, Codable, CaseIterable, Sendable {
    case retailerFeedListing = "retailer-feed-listing"
    case retailerObservation = "retailer-observation"
    case communityStoreReport = "community-store-report"
}

enum RemoteImagePurpose: String, Codable, CaseIterable, Sendable {
    case front
    case ingredients
    case barcode
    case nutrition
    case certification
}

enum PackageEvidencePurpose: String, Codable, CaseIterable, Sendable {
    case front
    case ingredients
    case barcode
    case nutrition
    case certification
}

enum EvidenceConsentState: String, Codable, CaseIterable, Sendable {
    case recorded
    case notRequired = "not-required"
}

enum EvidencePrivacyState: String, Codable, CaseIterable, Sendable {
    case screened
    case redacted
}

enum EvidenceReviewState: String, Codable, CaseIterable, Sendable {
    case unreviewed
    case inReview = "in-review"
    case approved
    case rejected
    case superseded
}

enum EvidenceReviewTargetType: String, Codable, CaseIterable, Sendable {
    case identity
    case ingredient
    case retailer
    case packageEvidence = "package-evidence"
    case certification
    case assessment
}

enum AssessmentValidityKind: String, Codable, CaseIterable, Sendable {
    case invalidated
    case superseded
    case restored
}

struct EvidenceSourceReference: Hashable, Codable, Sendable {
    let sourceKey: String
    let `operator`: String
    let sourceClass: EvidenceSourceClass
    let reference: String
    let accessMethod: EvidenceSourceAccessMethod
    let markets: [String]
    let retrievedAt: Date
    let sourceSnapshotID: String?
    let sourceRevision: String?
    let sourceModifiedAt: Date?
}

struct ProductIdentityEvidence: Identifiable, Hashable, Codable, Sendable {
    let id: String
    let gtin: String
    let originalBarcode: String
    let market: String
    let sourceKey: String
    let sourceRecordID: String
    let sourceRevision: String?
    let name: String
    let brandOwner: String?
    let brand: String?
    let quantity: String?
    let categories: [String]?
    let packaging: [String]?
    let observedAt: Date?
    let retrievedAt: Date
    let sourceModifiedAt: Date?
    let confidence: EvidenceIdentityConfidence
}

struct EvidenceTransformationMetadata: Hashable, Codable, Sendable {
    let tool: String?
    let version: String?
    let confidence: Double?
    let language: String?
}

struct IngredientEvidence: Identifiable, Hashable, Codable, Sendable {
    let id: String
    let gtin: String
    let market: String
    let sourceKey: String
    let sourceRecordID: String
    let sourceRevision: String?
    let ingredientsText: String
    let languageCode: String
    let allergensText: String?
    let tracesText: String?
    let observedAt: Date?
    let retrievedAt: Date
    let sourceModifiedAt: Date?
    let contentHash: String
    let captureMethod: IngredientCaptureMethod
    let verificationState: EvidenceVerificationState
    let supersedesID: String?
    let transformation: EvidenceTransformationMetadata?
}

struct RetailerEvidenceRecord: Identifiable, Hashable, Codable, Sendable {
    let id: String
    let kind: RetailerEvidenceKind
    let retailerKey: String
    let gtin: String
    let market: String
    let sourceKey: String
    let sourceRecordID: String
    let sourceRevision: String?
    let observedAt: Date?
    let snapshotAt: Date?
    let retrievedAt: Date
    let locationID: String?
    let scope: String?
    let confidence: String
    let limitations: String
}

struct RemoteProductImageReference: Identifiable, Hashable, Codable, Sendable {
    let id: String
    let gtin: String
    let market: String
    let purpose: RemoteImagePurpose
    let url: URL
    let sourceKey: String
    let imageID: String
    let revision: String?
    let retrievedAt: Date
    let sourceModifiedAt: Date?
    let width: Int?
    let height: Int?
}

struct PackageEvidenceReference: Identifiable, Hashable, Codable, Sendable {
    let id: String
    let gtin: String
    let market: String
    let purpose: PackageEvidencePurpose
    let sha256: String
    let observedAt: Date
    let consentState: EvidenceConsentState
    let privacyState: EvidencePrivacyState
    let verificationState: EvidenceVerificationState
    let internalReference: String
    let redactionState: String?
}

struct ProductCertificationEvidence: Identifiable, Hashable, Codable, Sendable {
    let id: String
    let certifier: String
    let scheme: String
    let certificateReference: String
    let gtin: String
    let market: String
    let matchBasis: String
    let scope: String
    let sourceKey: String
    let sourceRecordID: String
    let sourceRevision: String?
    let retrievedAt: Date
    let lastCheckedAt: Date
    let issueAt: Date?
    let effectiveAt: Date?
    let expiryAt: Date?
    let revokedAt: Date?
    let suspendedAt: Date?
    let facility: String?
    let batch: String?
    let evidenceHash: String?
}

struct EvidenceReviewRecord: Identifiable, Hashable, Codable, Sendable {
    let id: String
    let targetID: String
    let targetType: EvidenceReviewTargetType
    let state: EvidenceReviewState
    let reviewerID: String
    let reviewedAt: Date
    let decisionCode: String
    let reason: String
    let methodologyVersion: String?
    let toolContext: String?
}

struct EvidenceAssessmentReason: Hashable, Codable, Sendable {
    let code: String
    let title: String
    let detail: String
    let severity: EvidenceSeverity
    let evidenceIDs: [String]
    let ingredient: String?
}

struct EvidenceAssessmentRecord: Identifiable, Hashable, Codable, Sendable {
    let id: String
    let gtin: String
    let market: String
    let status: HalalStatus
    let methodologyVersion: String
    let assessedAt: Date
    let ingredientObservationID: String?
    let certificationIDs: [String]
    let evidenceIDs: [String]
    let reasons: [EvidenceAssessmentReason]
    let recheckAt: Date?
}

struct AssessmentValidityEvent: Identifiable, Hashable, Codable, Sendable {
    let id: String
    let assessmentID: String
    let kind: AssessmentValidityKind
    let occurredAt: Date
    let reason: String
    let triggeredByEvidenceID: String?
}

struct CurrentEvidenceSelection: Identifiable, Hashable, Codable, Sendable {
    let id: String
    let gtin: String
    let market: String
    let identityObservationID: String
    let ingredientObservationID: String?
    let assessmentID: String?
    let certificationIDs: [String]
    let retailerEvidenceIDs: [String]
    let remoteImageIDs: [String]
    let conflictFlags: [String]
}

struct CatalogEvidenceSourceSnapshot: Hashable, Codable, Sendable {
    let sourceKey: String
    let snapshotID: String
    let digest: String
    let retrievedAt: Date
}

struct CatalogReleaseEvidence: Identifiable, Hashable, Codable, Sendable {
    let id: String
    let catalogVersion: String
    let methodologyVersion: String
    let selectionPolicyVersion: String
    let generatedAt: Date
    let builderVersion: String
    let commitSHA: String
    let runtimeDigest: String
    let sourceSnapshots: [CatalogEvidenceSourceSnapshot]
    let counts: [String: Int]
}

struct EvidenceEnvelopeV1: Hashable, Codable, Sendable {
    let schemaVersion: Int
    let sources: [EvidenceSourceReference]
    let identities: [ProductIdentityEvidence]
    let ingredients: [IngredientEvidence]
    let retailerEvidence: [RetailerEvidenceRecord]
    let remoteImages: [RemoteProductImageReference]
    let packageEvidence: [PackageEvidenceReference]
    let certifications: [ProductCertificationEvidence]
    let reviews: [EvidenceReviewRecord]
    let assessments: [EvidenceAssessmentRecord]
    let validityEvents: [AssessmentValidityEvent]
    let currentSelections: [CurrentEvidenceSelection]
    let releases: [CatalogReleaseEvidence]
}
