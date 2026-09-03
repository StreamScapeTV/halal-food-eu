import Testing
@testable import HalalFoodEU

@Suite("Ingredient OCR reading order")
struct IngredientOCRReadingOrderTests {
    @Test("Lines sort top-to-bottom and left-to-right in Vision coordinates")
    func readingOrderIsDeterministic() {
        let lines = [
            line("bottom", x: 0.1, y: 0.1),
            line("top-right", x: 0.6, y: 0.8),
            line("top-left", x: 0.1, y: 0.8),
            line("middle", x: 0.1, y: 0.45)
        ]

        let sorted = IngredientOCRReadingOrder.sorted(lines)

        #expect(sorted.map(\.text) == ["top-left", "top-right", "middle", "bottom"])
    }

    @Test("Result confidence is the arithmetic mean of line confidence")
    func averageConfidence() {
        let result = IngredientOCRResult(
            visionRevision: "revision3",
            effectiveRecognitionLanguages: ["de-DE"],
            lines: [
                line("one", x: 0.1, y: 0.8, confidence: 0.8),
                line("two", x: 0.1, y: 0.5, confidence: 1.0)
            ]
        )

        #expect(abs(result.averageConfidence - 0.9) < 0.000_001)
        #expect(result.transcript == "one\ntwo")
    }

    private func line(
        _ text: String,
        x: Double,
        y: Double,
        confidence: Double = 0.9
    ) -> IngredientOCRLine {
        IngredientOCRLine(
            text: text,
            confidence: confidence,
            languages: ["en-US"],
            boundingBox: IngredientOCRBoundingBox(x: x, y: y, width: 0.3, height: 0.08)
        )
    }
}
