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
    private let cameraHistoryConsentToken: @MainActor @Sendable () -> UInt64?
    private let onCameraScanResolved: @MainActor @Sendable (ProductLookupResult, UInt64) -> Void
    private var lookupTask: Task<Void, Never>?
    private var lastRequest: (payload: String, symbology: Barcode.SymbologyHint)?

    init(
        lookupProduct: LookupProductByBarcode,
        cameraHistoryConsentToken: @escaping @MainActor @Sendable () -> UInt64? = { nil },
        onCameraScanResolved: @escaping @MainActor @Sendable (ProductLookupResult, UInt64) -> Void = { _, _ in }
    ) {
        self.lookupProduct = lookupProduct
        self.cameraHistoryConsentToken = cameraHistoryConsentToken
        self.onCameraScanResolved = onCameraScanResolved
    }

    func submitManualBarcode() {
        submit(manualBarcode, symbology: .unknown)
    }

    func acceptScan(_ scan: ScannedBarcode) {
        isScannerPresented = false
        manualBarcode = scan.payload
        // Consent belongs to the physical camera event. Capture the current
        // generation synchronously and carry it unchanged through resolution.
        let consentToken = cameraHistoryConsentToken()
        submit(
            scan.payload,
            symbology: scan.symbology,
            cameraHistoryConsentToken: consentToken
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
        cameraHistoryConsentToken consentToken: UInt64? = nil
    ) {
        lookupTask?.cancel()
        lastRequest = (payload, symbology)
        lookupState = .lookingUp

        lookupTask = Task { [weak self, lookupProduct, onCameraScanResolved] in
            do {
                let result = try await lookupProduct(payload, symbology: symbology)
                try Task.checkCancellation()

                guard let self else { return }
                if let consentToken {
                    onCameraScanResolved(result, consentToken)
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
