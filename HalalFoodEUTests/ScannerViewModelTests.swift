import Foundation
import Testing
@testable import HalalFoodEU

@Suite("Scanner lookup state")
@MainActor
struct ScannerViewModelTests {
    @Test("A newer scan supersedes an obsolete lookup and wins the UI state")
    func rapidScanSupersedesObsoleteLookup() async throws {
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

        // The slow catalog intentionally ignores cancellation so the obsolete
        // lookup still returns. The cancelled result task must not publish it.
        try await Task.sleep(for: .milliseconds(320))
        #expect(viewModel.lookupState == .notFound(second))
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

    @Test("Selecting a search summary performs the existing exact barcode lookup")
    func searchSelectionUsesExactLookupPath() async throws {
        let catalog = CapturingCatalog()
        let viewModel = ScannerViewModel(
            lookupProduct: LookupProductByBarcode(catalog: catalog)
        )
        let selected = try Barcode(validating: "0200000000004")

        viewModel.lookup(selected)

        try await waitUntil {
            if case let .notFound(barcode) = viewModel.lookupState {
                return barcode == selected
            }
            return false
        }
        let lookups = await catalog.lookups
        #expect(viewModel.manualBarcode == selected.rawValue)
        #expect(lookups == [selected.rawValue])
    }

    private func waitUntilLookupStarted(
        _ barcode: String,
        in catalog: DelayedCatalog,
        attempts: Int = 100
    ) async throws {
        for _ in 0..<attempts {
            if (await catalog.startedLookups).contains(barcode) { return }
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

    init(slowBarcode: String) {
        self.slowBarcode = slowBarcode
    }

    func product(for barcode: Barcode) async throws -> ProductRecord? {
        startedLookups.append(barcode.rawValue)
        if barcode.rawValue == slowBarcode {
            await Task.detached {
                try? await Task.sleep(for: .milliseconds(250))
            }.value
        } else {
            try await Task.sleep(for: .milliseconds(10))
        }
        return nil
    }
}

private actor FailingCatalog: ProductCatalog {
    func product(for barcode: Barcode) async throws -> ProductRecord? {
        throw ProductCatalogError.unavailable("fixture catalog unavailable")
    }
}

private actor CapturingCatalog: ProductCatalog {
    private(set) var lookups: [String] = []

    func product(for barcode: Barcode) async throws -> ProductRecord? {
        lookups.append(barcode.rawValue)
        return nil
    }
}
