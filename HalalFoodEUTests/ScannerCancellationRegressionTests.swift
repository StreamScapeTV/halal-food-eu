import Foundation
import Testing
@testable import HalalFoodEU

@Suite("Scanner cancellation regressions")
@MainActor
struct ScannerCancellationRegressionTests {
    @Test("A cancelled lookup cannot publish a later non-cancellation failure")
    func cancelledLookupCannotOverwriteNewerState() async throws {
        let first = try Barcode(validating: "0200000000035")
        let second = try Barcode(validating: "4006381333931")
        let catalog = CancellationIgnoringFailingCatalog(slowBarcode: first.rawValue)
        let viewModel = ScannerViewModel(
            lookupProduct: LookupProductByBarcode(catalog: catalog)
        )

        viewModel.tryDemoBarcode(first.rawValue)
        await Task.yield()
        viewModel.tryDemoBarcode(second.rawValue)

        try await waitUntil {
            if case let .notFound(barcode) = viewModel.lookupState {
                return barcode == second
            }
            return false
        }

        try await Task.sleep(for: .milliseconds(220))
        #expect(viewModel.lookupState == .notFound(second))
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

private actor CancellationIgnoringFailingCatalog: ProductCatalog {
    let slowBarcode: String

    init(slowBarcode: String) {
        self.slowBarcode = slowBarcode
    }

    func product(for barcode: Barcode) async throws -> ProductRecord? {
        if barcode.rawValue == slowBarcode {
            await Task.detached {
                try? await Task.sleep(for: .milliseconds(150))
            }.value
            throw ProductCatalogError.unavailable("obsolete lookup failure")
        }

        try await Task.sleep(for: .milliseconds(10))
        return nil
    }
}
