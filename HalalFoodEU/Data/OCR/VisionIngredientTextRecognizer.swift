import CoreGraphics
import Foundation
import ImageIO
import Vision

actor VisionIngredientTextRecognizer: IngredientTextRecognizing {
    private static let maxInputBytes = 24 * 1024 * 1024
    private static let maxSourceDimension = 12_000
    private static let maxSourcePixels: Int64 = 64_000_000
    private static let maxOCRDimension = 3_000
    private static let minSourceDimension = 96

    func recognize(
        imageData: Data,
        preferredLanguages: [String] = ["de-DE", "en-US"]
    ) async throws -> IngredientOCRResult {
        try Task.checkCancellation()
        let image = try prepareImage(from: imageData)
        try Task.checkCancellation()

        var request = RecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.usesLanguageCorrection = false
        request.automaticallyDetectsLanguage = false

        let supportedLanguages = request.supportedRecognitionLanguages
        let preferred = preferredLanguages.map { identifier in
            (identifier, Locale.Language(identifier: identifier))
        }
        let effective = preferred.filter { _, requested in
            supportedLanguages.contains { supported in
                supported.isEquivalent(to: requested)
            }
        }

        if effective.isEmpty {
            request.automaticallyDetectsLanguage = true
        } else {
            request.recognitionLanguages = effective.map(\.1)
        }

        let observations = try await request.perform(on: image, orientation: .up)
        try Task.checkCancellation()

        let lines = observations.compactMap { observation -> IngredientOCRLine? in
            guard let candidate = observation.topCandidates(1).first else { return nil }
            let text = candidate.string.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !text.isEmpty else { return nil }

            let rect = observation.boundingBox.cgRect
            return IngredientOCRLine(
                text: text,
                confidence: Double(candidate.confidence),
                languages: observation.recognitionLanguages.map(\.minimalIdentifier),
                boundingBox: IngredientOCRBoundingBox(
                    x: Double(rect.origin.x),
                    y: Double(rect.origin.y),
                    width: Double(rect.width),
                    height: Double(rect.height)
                )
            )
        }

        return IngredientOCRResult(
            visionRevision: String(describing: request.revision),
            effectiveRecognitionLanguages: effective.map(\.0),
            lines: IngredientOCRReadingOrder.sorted(lines)
        )
    }

    private func prepareImage(from data: Data) throws -> CGImage {
        guard !data.isEmpty else {
            throw IngredientTextRecognitionError.emptyImage
        }
        guard data.count <= Self.maxInputBytes else {
            throw IngredientTextRecognitionError.inputTooLarge
        }
        guard let source = CGImageSourceCreateWithData(data as CFData, nil) else {
            throw IngredientTextRecognitionError.unreadableImage
        }
        guard
            let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
            let width = (properties[kCGImagePropertyPixelWidth] as? NSNumber)?.intValue,
            let height = (properties[kCGImagePropertyPixelHeight] as? NSNumber)?.intValue
        else {
            throw IngredientTextRecognitionError.unreadableImage
        }

        guard width >= Self.minSourceDimension, height >= Self.minSourceDimension else {
            throw IngredientTextRecognitionError.imageTooSmall
        }
        guard width <= Self.maxSourceDimension, height <= Self.maxSourceDimension else {
            throw IngredientTextRecognitionError.unsafeDimensions
        }

        let pixelCount = Int64(width) * Int64(height)
        guard pixelCount > 0, pixelCount <= Self.maxSourcePixels else {
            throw IngredientTextRecognitionError.unsafeDimensions
        }

        let options: [CFString: Any] = [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceCreateThumbnailWithTransform: true,
            kCGImageSourceThumbnailMaxPixelSize: Self.maxOCRDimension,
            kCGImageSourceShouldCacheImmediately: true
        ]
        guard let image = CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary) else {
            throw IngredientTextRecognitionError.unreadableImage
        }
        return image
    }
}
