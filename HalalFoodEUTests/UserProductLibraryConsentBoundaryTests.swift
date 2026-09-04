import Foundation
import Testing
@testable import HalalFoodEU

@Suite("Local history consent boundary")
@MainActor
struct UserProductLibraryConsentBoundaryTests {
    @Test("A scan that occurs while history is off cannot be admitted by a later opt-in")
    func preOptInScanCannotRaceIntoHistory() async throws {
        let barcode = try Barcode(validating: "4006381333931")
        let store = ConsentBoundaryStore()
        let viewModel = UserProductLibraryViewModel(
            store: store,
            resolveSavedProduct: ResolveSavedProduct(
                catalog: EmptyConsentBoundaryCatalog(),
                currentCatalogVersion: "fixture-v1"
            ),
            currentCatalogVersion: "fixture-v1",
            now: { Date(timeIntervalSince1970: 1_700_000_000) }
        )

        await viewModel.load()
        #expect(viewModel.historyEnabled == false)

        viewModel.recordCameraScan(ProductLookupResult(barcode: barcode, product: nil))
        await viewModel.setHistoryEnabled(true)
        try await Task.sleep(for: .milliseconds(50))

        let recordedBarcodes = await store.recordedBarcodes
        #expect(recordedBarcodes.isEmpty)
        #expect(viewModel.historyEnabled)
    }
}

private actor ConsentBoundaryStore: UserProductLibraryStore {
    private var enabled = false
    private(set) var recordedBarcodes: [Barcode] = []

    func isHistoryEnabled() async throws -> Bool { enabled }

    func setHistoryEnabled(_ enabled: Bool) async throws {
        self.enabled = enabled
    }

    func recordScan(
        barcode: Barcode,
        scannedAt: Date,
        catalogVersion: String,
        versionMarker: SavedProductVersionMarker
    ) async throws {
        // Deliberately model the original race: if the view model incorrectly
        // dispatches a pre-consent scan, the store would accept it once enabled.
        guard enabled else { return }
        recordedBarcodes.append(barcode)
    }

    func history(limit: Int) async throws -> [ScanHistoryEntry] { [] }
    func deleteHistoryEntry(id: Int64) async throws {}
    func clearHistory() async throws {}
    func favorites() async throws -> [FavoriteProduct] { [] }
    func favorite(for barcode: Barcode) async throws -> FavoriteProduct? { nil }

    func setFavorite(
        barcode: Barcode,
        savedAt: Date,
        catalogVersion: String,
        versionMarker: SavedProductVersionMarker,
        isFavorite: Bool
    ) async throws {}
}

private actor EmptyConsentBoundaryCatalog: ProductCatalog {
    func product(for barcode: Barcode) async throws -> ProductRecord? { nil }
}
