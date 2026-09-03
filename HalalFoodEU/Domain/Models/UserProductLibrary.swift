import Foundation

struct SavedProductVersionMarker: Codable, Equatable, Sendable {
    let wasPresent: Bool
    let name: String?
    let brand: String?
    let ingredientContentHash: String?
    let assessmentStatus: HalalStatus?
    let methodologyVersion: String?
    let reviewedAt: Date?
    let reasonCodes: [String]
    let certificationReferences: [String]
    let conflictFlags: [String]
    let retailerEvidenceKeys: [String]

    init(product: ProductRecord?) {
        guard let product else {
            wasPresent = false
            name = nil
            brand = nil
            ingredientContentHash = nil
            assessmentStatus = nil
            methodologyVersion = nil
            reviewedAt = nil
            reasonCodes = []
            certificationReferences = []
            conflictFlags = []
            retailerEvidenceKeys = []
            return
        }

        wasPresent = true
        name = product.name
        brand = product.brand
        ingredientContentHash = product.observation?.contentHash
        assessmentStatus = product.assessment.status
        methodologyVersion = product.assessment.methodologyVersion
        reviewedAt = product.assessment.reviewedAt
        reasonCodes = product.assessment.reasons.map(\.code).sorted()
        certificationReferences = product.assessment.certifications
            .map { "\($0.certifyingBody)|\($0.certificateReference)|\($0.scope)" }
            .sorted()
        conflictFlags = product.details?.conflictFlags.sorted() ?? []
        retailerEvidenceKeys = product.details?.retailerEvidence
            .map {
                let observedAt = $0.observedAt?.ISO8601Format() ?? ""
                let snapshotAt = $0.snapshotAt?.ISO8601Format() ?? ""
                return "\($0.kind.rawValue)|\($0.retailerKey)|\(observedAt)|\(snapshotAt)|\($0.source.reference)"
            }
            .sorted() ?? []
    }
}

enum SavedProductChangeState: Equatable, Sendable {
    case unchanged
    case changed
    case noLongerPresent
    case nowAvailable
}

extension SavedProductVersionMarker {
    func comparison(with currentProduct: ProductRecord?) -> SavedProductChangeState {
        let current = SavedProductVersionMarker(product: currentProduct)
        switch (wasPresent, current.wasPresent) {
        case (true, false):
            return .noLongerPresent
        case (false, true):
            return .nowAvailable
        default:
            return self == current ? .unchanged : .changed
        }
    }
}

struct ScanHistoryEntry: Identifiable, Equatable, Sendable {
    let id: Int64
    let barcode: Barcode
    let scannedAt: Date
    let catalogVersion: String
    let versionMarker: SavedProductVersionMarker
}

struct FavoriteProduct: Identifiable, Equatable, Sendable {
    var id: String { barcode.rawValue }

    let barcode: Barcode
    let savedAt: Date
    let catalogVersion: String
    let versionMarker: SavedProductVersionMarker
}
