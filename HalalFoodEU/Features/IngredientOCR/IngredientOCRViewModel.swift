import Foundation
import Observation

@MainActor
@Observable
final class IngredientOCRViewModel {
    enum State: Equatable {
        case idle
        case recognizing
        case recognized(IngredientOCRResult)
        case unreadable
        case failed(String)
    }

    private(set) var state: State = .idle
    var editableText = ""

    private let recognizer: any IngredientTextRecognizing
    private var recognitionTask: Task<Void, Never>?
    private var generation = 0

    init(recognizer: any IngredientTextRecognizing) {
        self.recognizer = recognizer
    }

    func recognize(imageData: Data) {
        generation += 1
        let requestGeneration = generation
        recognitionTask?.cancel()
        state = .recognizing
        editableText = ""

        recognitionTask = Task { [weak self, recognizer] in
            do {
                let result = try await recognizer.recognize(
                    imageData: imageData,
                    preferredLanguages: ["de-DE", "en-US"]
                )
                try Task.checkCancellation()
                guard let self, requestGeneration == generation else { return }

                if result.lines.isEmpty {
                    state = .unreadable
                } else {
                    editableText = result.transcript
                    state = .recognized(result)
                }
            } catch is CancellationError {
                return
            } catch {
                guard let self, !Task.isCancelled, requestGeneration == generation else { return }
                state = .failed(error.localizedDescription)
            }
        }
    }

    func reset() {
        generation += 1
        recognitionTask?.cancel()
        recognitionTask = nil
        editableText = ""
        state = .idle
    }

    func cancelActiveRecognition() {
        generation += 1
        recognitionTask?.cancel()
        recognitionTask = nil
    }
}
