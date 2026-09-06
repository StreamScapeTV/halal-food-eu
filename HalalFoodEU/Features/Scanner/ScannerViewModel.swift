import Foundation
import Observation

@MainActor
@Observable
final class ScannerViewModel {
    enum LookupState: Equatable {
        case idle
        case lookingUp
        case found(ProductRecord)
        case notFound(Barcode)
        case invalidInput(String)
        case failed(String)
    }

    var manualBarcode = ""
    var isScannerPresented = false
    private(set) var lookupState: LookupState = .idle

    private let lookupProduct: LookupProductByBarcode
    private let shouldRecordCameraHistory: @MainActor @Sendable () -> Bool
    private let onCameraScanResolved: @MainActor @Sendable (ProductLookupResult) -> Void
    private var lookupTask: Task<Void, Never>?
    private var lastRequest: (payload: String, symbology: Barcode.SymbologyHint)?

    init(
        lookupProduct: LookupProductByBarcode,
        shouldRecordCameraHistory: @escaping @MainActor @Sendable () -> Bool = { true },
        onCameraScanResolved: @escaping @MainActor @Sendable (ProductLookupResult) -> Void = { _ in }
    ) {
        self.lookupProduct = lookupProduct
        self.shouldRecordCameraHistory = shouldRecordCameraHistory
        self.onCameraScanResolved = onCameraScanResolved
    }

    func submitManualBarcode() {
        submit(manualBarcode, symbology: .unknown)
    }

    func acceptScan(_ scan: ScannedBarcode) {
        isScannerPresented = false
        manualBarcode = scan.payload
        // Consent belongs to the physical scan event, not to the later async
        // catalog-resolution instant. Carry this immutable eligibility decision
        // through lookup so a later opt-in cannot admit a pre-consent scan.
        let recordCameraHistory = shouldRecordCameraHistory()
        submit(
            scan.payload,
            symbology: scan.symbology,
            recordCameraHistory: recordCameraHistory
        )
    }

    func lookup(_ barcode: Barcode) {
        manualBarcode = barcode.rawValue
        submit(barcode.rawValue, symbology: .retail)
    }

    func tryDemoBarcode(_ barcode: String) {
        manualBarcode = barcode
        submit(barcode, symbology: .retail)
    }

    func retry() {
        guard let lastRequest else { return }
        // A retry is a lookup action, not a second physical camera scan event.
        submit(lastRequest.payload, symbology: lastRequest.symbology)
    }

    func reset() {
        lookupTask?.cancel()
        lookupState = .idle
    }

    private func submit(
        _ payload: String,
        symbology: Barcode.SymbologyHint,
        recordCameraHistory: Bool = false
    ) {
        lookupTask?.cancel()
        lastRequest = (payload, symbology)
        lookupState = .lookingUp

        lookupTask = Task { [weak self, lookupProduct, onCameraScanResolved] in
            do {
                let result = try await lookupProduct(payload, symbology: symbology)
                try Task.checkCancellation()

                guard let self else { return }
                if recordCameraHistory {
                    onCameraScanResolved(result)
                }
                if let product = result.product {
                    lookupState = .found(product)
                } else {
                    lookupState = .notFound(result.barcode)
                }
            } catch is CancellationError {
                return
            } catch let error as Barcode.ValidationError {
                guard !Task.isCancelled else { return }
                self?.lookupState = .invalidInput(error.localizedDescription)
            } catch {
                guard !Task.isCancelled else { return }
                self?.lookupState = .failed(error.localizedDescription)
            }
        }
    }
}
