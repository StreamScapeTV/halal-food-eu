import Foundation

protocol IngredientTextRecognizing: Sendable {
    func recognize(
        imageData: Data,
        preferredLanguages: [String]
    ) async throws -> IngredientOCRResult
}
