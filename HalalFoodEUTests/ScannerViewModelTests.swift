import Foundation
import Testing
@testable import HalalFoodEU

@Suite("Scanner lookup state")
@MainActor
struct ScannerViewModelTests {
    @Test("A newer scan cancels an obsolete lookup and wins the UI state")
    func rapidScanCancelsObsoleteLookup() async throws {
        let first = try Barcode(validating: "0200000000035")
        let second = try Barcode(validating: "4006381333931")
        let catalog = DelayedCatalog(slowBarcode: first.rawValue)
        let viewModel = ScannerViewModel(
            lookupProduct: LookupProductByBarcode(catalog: catalog)
        )

        viewModel.tryDemoBarcode(first.rawValue)
        try await waitUntilLookupStarted(first.rawValue, in: catalog)
        viewModel.tryDemoBarcode(second.rawValue)

        try await waitUntil {
            if case let .notFound(barcode) = viewModel.lookupState {
                return barcode == second
            }
            return false
        }

        #expect(viewModel.lookupState == .notFound(second))
        let cancelled = await catalog.cancelledLookups
        #expect(cancelled.contains(first.rawValue))
    }

    @Test("Catalog failures preserve manual entry for retry or correction")
    func catalogFailurePreservesManualFallback() async throws {
        let rawBarcode = "4006381333931"
        let catalog = FailingCatalog()
        let viewModel = ScannerViewModel(
            lookupProduct: LookupProductByBarcode(catalog: catalog)
        )
        viewModel.manualBarcode = rawBarcode

        viewModel.submitManualBarcode()

        try await waitUntil {
            if case .failed = viewModel.lookupState { return true }
            return false
        }

        #expect(viewModel.manualBarcode == rawBarcode)
        if case let .failed(message) = viewModel.lookupState {
            #expect(message.contains("fixture catalog unavailable"))
        } else {
            Issue.record("Expected catalog failure state")
        }
    }

    private func waitUntilLookupStarted(
        _ barcode: String,
        in catalog: DelayedCatalog,
        attempts: Int = 100
    ) async throws {
        for _ in 0..<attempts {
            if await catalog.startedLookups.contains(barcode) { return }
            try await Task.sleep(for: .milliseconds(10))
        }
        Issue.record("Timed out waiting for lookup to start")
    }

    private func waitUntil(
        attempts: Int = 100,
        condition: @MainActor () -> Bool
    ) async throws {
        for _ in 0..<attempts {
            if condition() { return }
            try await Task.sleep(for: .milliseconds(10))
        }
        Issue.record("Timed out waiting for scanner state")
    }
}

private actor DelayedCatalog: ProductCatalog {
    let slowBarcode: String
    private(set) var startedLookups: [String] = []
    private(set) var cancelledLookups: [String] = []

    init(slowBarcode: String) {
        self.slowBarcode = slowBarcode
    }

    func product(for barcode: Barcode) async throws -> ProductRecord? {
        startedLookups.append(barcode.rawValue)
        do {
            if barcode.rawValue == slowBarcode {
                try await Task.sleep(for: .milliseconds(250))
            } else {
                try await Task.sleep(for: .milliseconds(10))
            }
            return nil
        } catch is CancellationError {
            cancelledLookups.append(barcode.rawValue)
            throw CancellationError()
        }
    }
}

private actor FailingCatalog: ProductCatalog {
    func product(for barcode: Barcode) async throws -> ProductRecord? {
        throw ProductCatalogError.unavailable("fixture catalog unavailable")
    }
}
