import Foundation

struct ProductSource: Codable, Equatable, Sendable {
    let name: String
    let kind: String
    let reference: String
    let license: String
    let retrievedAt: Date
}

struct IngredientObservation: Codable, Equatable, Sendable {
    let text: String
    let languageCode: String
    let observedAt: Date?
    let contentHash: String
    let freshness: EvidenceFreshness
    let source: ProductSource
}

struct ProductRecord: Codable, Equatable, Sendable {
    let barcode: Barcode
    let name: String
    let brand: String?
    let observation: IngredientObservation?
    let assessment: HalalAssessment
    let catalogVersion: String
}
