import Foundation
import Testing
import UIKit
@testable import HalalFoodEU

@Suite("Vision ingredient OCR")
struct VisionIngredientTextRecognizerTests {
    @Test("Swift-native Vision recognizes a deterministic ingredient label")
    func recognizesSyntheticIngredientPanel() async throws {
        let imageData = try await MainActor.run { try makeIngredientPanelJPEG() }
        let recognizer = VisionIngredientTextRecognizer()

        let result = try await recognizer.recognize(
            imageData: imageData,
            preferredLanguages: ["de-DE", "en-US"]
        )
        let normalized = result.transcript.lowercased()

        #expect(!result.lines.isEmpty)
        #expect(normalized.contains("zucker") || normalized.contains("sugar"))
        #expect(normalized.contains("kakao") || normalized.contains("cocoa"))
        #expect(result.effectiveRecognitionLanguages.contains("de-DE"))
        #expect(result.effectiveRecognitionLanguages.contains("en-US"))
        #expect(result.averageConfidence > 0)
        #expect(result.lines.allSatisfy { line in
            let box = line.boundingBox
            return box.x >= 0 && box.y >= 0 && box.width >= 0 && box.height >= 0
                && box.x <= 1 && box.y <= 1 && box.width <= 1 && box.height <= 1
        })
    }

    @Test("Empty and oversized bytes are rejected before Vision")
    func rejectsUnsafeInputBounds() async {
        let recognizer = VisionIngredientTextRecognizer()

        await #expect(throws: IngredientTextRecognitionError.emptyImage) {
            try await recognizer.recognize(imageData: Data(), preferredLanguages: ["de-DE"])
        }

        let oversized = Data(repeating: 0, count: (24 * 1024 * 1024) + 1)
        await #expect(throws: IngredientTextRecognitionError.inputTooLarge) {
            try await recognizer.recognize(imageData: oversized, preferredLanguages: ["de-DE"])
        }
    }

    @Test("Invalid image bytes are rejected explicitly")
    func rejectsUnreadableImages() async {
        let recognizer = VisionIngredientTextRecognizer()

        await #expect(throws: IngredientTextRecognitionError.unreadableImage) {
            try await recognizer.recognize(
                imageData: Data("not-an-image".utf8),
                preferredLanguages: ["de-DE"]
            )
        }
    }

    @Test("Tiny pixel images are rejected explicitly")
    func rejectsTinyImages() async throws {
        let recognizer = VisionIngredientTextRecognizer()
        let tinyData = try await MainActor.run { try makeTinyJPEG() }

        await #expect(throws: IngredientTextRecognitionError.imageTooSmall) {
            try await recognizer.recognize(imageData: tinyData, preferredLanguages: ["de-DE"])
        }
    }

    @MainActor
    private func makeTinyJPEG() throws -> Data {
        let size = CGSize(width: 64, height: 64)
        let format = UIGraphicsImageRendererFormat.default()
        format.scale = 1
        let tiny = UIGraphicsImageRenderer(size: size, format: format).image { context in
            UIColor.white.setFill()
            context.fill(CGRect(origin: .zero, size: size))
        }
        return try #require(tiny.jpegData(compressionQuality: 0.9))
    }

    @MainActor
    private func makeIngredientPanelJPEG() throws -> Data {
        let size = CGSize(width: 1800, height: 900)
        let image = UIGraphicsImageRenderer(size: size).image { context in
            UIColor.white.setFill()
            context.fill(CGRect(origin: .zero, size: size))

            let paragraph = NSMutableParagraphStyle()
            paragraph.lineSpacing = 18
            let attributes: [NSAttributedString.Key: Any] = [
                .font: UIFont.systemFont(ofSize: 76, weight: .semibold),
                .foregroundColor: UIColor.black,
                .paragraphStyle: paragraph
            ]
            let text = "Zutaten: Zucker, Kakao, Salz\nIngredients: sugar, cocoa, salt"
            text.draw(
                in: CGRect(x: 100, y: 180, width: 1600, height: 500),
                withAttributes: attributes
            )
        }
        return try #require(image.jpegData(compressionQuality: 0.95))
    }
}
