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
    private var lookupTask: Task<Void, Never>?
    private var lastRequest: (payload: String, symbology: Barcode.SymbologyHint)?

    init(lookupProduct: LookupProductByBarcode) {
        self.lookupProduct = lookupProduct
    }

    func submitManualBarcode() {
        submit(manualBarcode, symbology: .unknown)
    }

    func acceptScan(_ scan: ScannedBarcode) {
        isScannerPresented = false
        manualBarcode = scan.payload
        submit(scan.payload, symbology: scan.symbology)
    }

    func tryDemoBarcode(_ barcode: String) {
        manualBarcode = barcode
        submit(barcode, symbology: .retail)
    }

    func retry() {
        guard let lastRequest else { return }
        submit(lastRequest.payload, symbology: lastRequest.symbology)
    }

    func reset() {
        lookupTask?.cancel()
        lookupState = .idle
    }

    private func submit(_ payload: String, symbology: Barcode.SymbologyHint) {
        lookupTask?.cancel()
        lastRequest = (payload, symbology)
        lookupState = .lookingUp

        lookupTask = Task { [weak self, lookupProduct] in
            do {
                let result = try await lookupProduct(payload, symbology: symbology)
                try Task.checkCancellation()

                guard let self else { return }
                if let product = result.product {
                    lookupState = .found(product)
                } else {
                    lookupState = .notFound(result.barcode)
                }
            } catch is CancellationError {
                return
            } catch let error as Barcode.ValidationError {
                self?.lookupState = .invalidInput(error.localizedDescription)
            } catch {
                self?.lookupState = .failed(error.localizedDescription)
            }
        }
    }
}
