import Foundation

struct IngredientOCRBoundingBox: Equatable, Sendable {
    let x: Double
    let y: Double
    let width: Double
    let height: Double

    var midY: Double { y + (height / 2) }
}

struct IngredientOCRLine: Equatable, Sendable {
    let text: String
    let confidence: Double
    let languages: [String]
    let boundingBox: IngredientOCRBoundingBox
}

struct IngredientOCRResult: Equatable, Sendable {
    let visionRevision: String
    let effectiveRecognitionLanguages: [String]
    let lines: [IngredientOCRLine]

    var transcript: String {
        lines.map(\.text).joined(separator: "\n")
    }

    var averageConfidence: Double {
        guard !lines.isEmpty else { return 0 }
        return lines.reduce(0) { $0 + $1.confidence } / Double(lines.count)
    }
}

enum IngredientOCRReadingOrder {
    static func sorted(_ lines: [IngredientOCRLine]) -> [IngredientOCRLine] {
        lines.sorted { lhs, rhs in
            let rowTolerance = max(lhs.boundingBox.height, rhs.boundingBox.height) * 0.5
            let verticalDelta = abs(lhs.boundingBox.midY - rhs.boundingBox.midY)

            if verticalDelta > rowTolerance {
                // Vision normalized coordinates have their origin at the lower-left.
                return lhs.boundingBox.midY > rhs.boundingBox.midY
            }
            if lhs.boundingBox.x != rhs.boundingBox.x {
                return lhs.boundingBox.x < rhs.boundingBox.x
            }
            if lhs.boundingBox.y != rhs.boundingBox.y {
                return lhs.boundingBox.y > rhs.boundingBox.y
            }
            return lhs.text.localizedStandardCompare(rhs.text) == .orderedAscending
        }
    }
}

enum IngredientTextRecognitionError: Error, Equatable, Sendable {
    case emptyImage
    case inputTooLarge
    case unreadableImage
    case imageTooSmall
    case unsafeDimensions
}

extension IngredientTextRecognitionError: LocalizedError {
    var errorDescription: String? {
        switch self {
        case .emptyImage:
            String(localized: "The captured ingredients photo is empty.", table: "IngredientOCR")
        case .inputTooLarge:
            String(localized: "The captured ingredients photo is too large to process safely.", table: "IngredientOCR")
        case .unreadableImage:
            String(localized: "The captured ingredients photo could not be read.", table: "IngredientOCR")
        case .imageTooSmall:
            String(localized: "The captured ingredients photo is too small for reliable text recognition.", table: "IngredientOCR")
        case .unsafeDimensions:
            String(localized: "The captured ingredients photo has unsafe dimensions.", table: "IngredientOCR")
        }
    }
}
