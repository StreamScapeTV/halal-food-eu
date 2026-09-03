import Foundation
import Testing
@testable import HalalFoodEU

@Suite("Ingredient OCR view-model state")
@MainActor
struct IngredientOCRViewModelTests {
    @Test("Recognized text is editable and preserves OCR metadata")
    func successPublishesEditableText() async throws {
        let result = IngredientOCRResult(
            visionRevision: "revision3",
            effectiveRecognitionLanguages: ["de-DE", "en-US"],
            lines: [
                IngredientOCRLine(
                    text: "Zutaten: Zucker, Kakao",
                    confidence: 0.94,
                    languages: ["de-DE"],
                    boundingBox: IngredientOCRBoundingBox(x: 0.1, y: 0.7, width: 0.8, height: 0.1)
                )
            ]
        )
        let viewModel = IngredientOCRViewModel(recognizer: SuccessfulIngredientRecognizer(result: result))

        viewModel.recognize(imageData: Data([1]))
        try await waitUntil { viewModel.state == .recognized(result) }

        #expect(viewModel.editableText == result.transcript)
        viewModel.editableText.append(" (checked)")
        #expect(viewModel.editableText.hasSuffix("(checked)"))
    }

    @Test("No recognized lines becomes a recoverable unreadable state")
    func emptyRecognitionIsUnreadable() async throws {
        let result = IngredientOCRResult(
            visionRevision: "revision3",
            effectiveRecognitionLanguages: ["de-DE", "en-US"],
            lines: []
        )
        let viewModel = IngredientOCRViewModel(recognizer: SuccessfulIngredientRecognizer(result: result))

        viewModel.recognize(imageData: Data([1]))
        try await waitUntil { viewModel.state == .unreadable }

        #expect(viewModel.editableText.isEmpty)
    }

    @Test("Recognition failures remain distinct from unreadable text")
    func recognitionFailureIsExplicit() async throws {
        let viewModel = IngredientOCRViewModel(recognizer: FailingIngredientRecognizer())

        viewModel.recognize(imageData: Data([1]))
        try await waitUntil {
            if case .failed = viewModel.state { return true }
            return false
        }

        if case let .failed(message) = viewModel.state {
            #expect(!message.isEmpty)
        } else {
            Issue.record("Expected failed OCR state")
        }
    }

    @Test("A newer photo supersedes a slow obsolete OCR result")
    func newerPhotoSupersedesObsoleteRecognition() async throws {
        let recognizer = DelayedIngredientRecognizer()
        let viewModel = IngredientOCRViewModel(recognizer: recognizer)

        viewModel.recognize(imageData: Data([1]))
        try await waitUntil { await recognizer.startedInputs.contains(1) }
        viewModel.recognize(imageData: Data([2]))

        try await waitUntil {
            if case let .recognized(result) = viewModel.state {
                return result.transcript == "second"
            }
            return false
        }

        try await Task.sleep(for: .milliseconds(300))
        if case let .recognized(result) = viewModel.state {
            #expect(result.transcript == "second")
        } else {
            Issue.record("Expected the newer OCR result to remain visible")
        }
    }

    private func waitUntil(
        attempts: Int = 150,
        condition: @MainActor () async -> Bool
    ) async throws {
        for _ in 0..<attempts {
            if await condition() { return }
            try await Task.sleep(for: .milliseconds(10))
        }
        Issue.record("Timed out waiting for OCR state")
    }
}

private struct SuccessfulIngredientRecognizer: IngredientTextRecognizing {
    let result: IngredientOCRResult

    func recognize(
        imageData: Data,
        preferredLanguages: [String]
    ) async throws -> IngredientOCRResult {
        result
    }
}

private struct FailingIngredientRecognizer: IngredientTextRecognizing {
    func recognize(
        imageData: Data,
        preferredLanguages: [String]
    ) async throws -> IngredientOCRResult {
        throw IngredientTextRecognitionError.unreadableImage
    }
}

private actor DelayedIngredientRecognizer: IngredientTextRecognizing {
    private(set) var startedInputs: [UInt8] = []

    func recognize(
        imageData: Data,
        preferredLanguages: [String]
    ) async throws -> IngredientOCRResult {
        let marker = imageData.first ?? 0
        startedInputs.append(marker)

        if marker == 1 {
            await Task.detached {
                try? await Task.sleep(for: .milliseconds(220))
            }.value
        } else {
            try await Task.sleep(for: .milliseconds(10))
        }

        let line = IngredientOCRLine(
            text: marker == 1 ? "first" : "second",
            confidence: 0.9,
            languages: ["en-US"],
            boundingBox: IngredientOCRBoundingBox(x: 0.1, y: 0.5, width: 0.8, height: 0.1)
        )
        return IngredientOCRResult(
            visionRevision: "revision3",
            effectiveRecognitionLanguages: preferredLanguages,
            lines: [line]
        )
    }
}
