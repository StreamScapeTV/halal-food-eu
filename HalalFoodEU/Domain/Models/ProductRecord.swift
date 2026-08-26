import Foundation

struct ProductSource: Hashable, Codable, Sendable {
    let name: String
    let kind: String
    let reference: String
    let license: String
    let retrievedAt: Date
}

struct IngredientObservation: Hashable, Codable, Sendable {
    let text: String
    let languageCode: String
    let observedAt: Date
    let contentHash: String
    let source: ProductSource
}

struct ProductRecord: Identifiable, Hashable, Codable, Sendable {
    var id: Barcode { barcode }

    let barcode: Barcode
    let name: String
    let brand: String?
    let observation: IngredientObservation
    let assessment: HalalAssessment
    let catalogVersion: String
}
