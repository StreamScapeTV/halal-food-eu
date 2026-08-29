import SwiftUI
import UIKit
import Vision
import VisionKit

struct ScannedBarcode: Sendable {
    let payload: String
    let symbology: Barcode.SymbologyHint
}

struct ScannerSheet: View {
    @Environment(\.dismiss) private var dismiss
    @State private var scannerError: String?

    let onScan: @MainActor (ScannedBarcode) -> Void

    var body: some View {
        NavigationStack {
            Group {
                if DataScannerViewController.isSupported, DataScannerViewController.isAvailable {
                    DataScannerCameraView(
                        onScan: onScan,
                        onFailure: { error in scannerError = error.localizedDescription }
                    )
                    .ignoresSafeArea(edges: .bottom)
                    .accessibilityLabel("Barcode scanner camera")
                    .accessibilityHint("Hold an EAN, UPC, or GS1 barcode inside the camera view.")
                } else {
                    ContentUnavailableView(
                        "Scanner unavailable",
                        systemImage: "barcode.viewfinder",
                        description: Text("Close the scanner and enter the barcode manually.")
                    )
                }
            }
            .navigationTitle("Scan barcode")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Close") { dismiss() }
                }
            }
            .alert("Scanner error", isPresented: Binding(
                get: { scannerError != nil },
                set: { if !$0 { scannerError = nil } }
            )) {
                Button("Close") { dismiss() }
            } message: {
                Text(scannerError ?? "The camera scanner could not start.")
            }
        }
    }
}

@MainActor
private struct DataScannerCameraView: UIViewControllerRepresentable {
    let onScan: @MainActor (ScannedBarcode) -> Void
    let onFailure: @MainActor (Error) -> Void

    func makeCoordinator() -> Coordinator {
        Coordinator(onScan: onScan, onFailure: onFailure)
    }

    func makeUIViewController(context: Context) -> DataScannerViewController {
        let controller = DataScannerViewController(
            recognizedDataTypes: [
                .barcode(symbologies: [.ean8, .ean13, .upce, .code128, .qr])
            ],
            qualityLevel: .balanced,
            recognizesMultipleItems: false,
            isHighFrameRateTrackingEnabled: true,
            isPinchToZoomEnabled: true,
            isGuidanceEnabled: true,
            isHighlightingEnabled: true
        )
        controller.delegate = context.coordinator
        return controller
    }

    func updateUIViewController(_ controller: DataScannerViewController, context: Context) {
        guard !context.coordinator.hasStarted else { return }
        context.coordinator.hasStarted = true

        do {
            try controller.startScanning()
        } catch {
            context.coordinator.onFailure(error)
        }
    }

    static func dismantleUIViewController(
        _ controller: DataScannerViewController,
        coordinator: Coordinator
    ) {
        controller.stopScanning()
    }

    @MainActor
    final class Coordinator: NSObject, DataScannerViewControllerDelegate {
        var hasStarted = false
        private var hasDeliveredResult = false
        let onScan: @MainActor (ScannedBarcode) -> Void
        let onFailure: @MainActor (Error) -> Void

        init(
            onScan: @escaping @MainActor (ScannedBarcode) -> Void,
            onFailure: @escaping @MainActor (Error) -> Void
        ) {
            self.onScan = onScan
            self.onFailure = onFailure
        }

        func dataScanner(
            _ dataScanner: DataScannerViewController,
            didAdd addedItems: [RecognizedItem],
            allItems: [RecognizedItem]
        ) {
            guard !hasDeliveredResult else { return }

            for item in addedItems {
                guard case let .barcode(barcode) = item,
                      let payload = barcode.payloadStringValue else {
                    continue
                }

                hasDeliveredResult = true
                UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                onScan(
                    ScannedBarcode(
                        payload: payload,
                        symbology: Self.hint(for: barcode.observation.symbology)
                    )
                )
                return
            }
        }

        func dataScanner(
            _ dataScanner: DataScannerViewController,
            becameUnavailableWithError error: DataScannerViewController.ScanningUnavailable
        ) {
            onFailure(error)
        }

        private static func hint(for symbology: VNBarcodeSymbology) -> Barcode.SymbologyHint {
            switch symbology {
            case .ean8:
                .ean8
            case .upce:
                .upce
            case .ean13:
                .retail
            case .qr:
                .qr
            case .code128:
                .code128
            default:
                .unknown
            }
        }
    }
}
