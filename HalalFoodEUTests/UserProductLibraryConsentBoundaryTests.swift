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
        let viewModel = makeLibraryViewModel(store: store)

        await viewModel.load()
        #expect(viewModel.historyEnabled == false)
        #expect(viewModel.cameraHistoryConsentToken() == nil)

        viewModel.recordCameraScan(
            ProductLookupResult(barcode: barcode, product: nil),
            consentToken: 0
        )
        await viewModel.setHistoryEnabled(true)
        try await Task.sleep(for: .milliseconds(50))

        let recordedBarcodes = await store.recordedBarcodes
        #expect(recordedBarcodes.isEmpty)
        #expect(viewModel.historyEnabled)
    }

    @Test("A physical scan captures consent before a delayed lookup resolves")
    func scannerCapturesConsentAtPhysicalScanBoundary() async throws {
        let rawBarcode = "4006381333931"
        let barcode = try Barcode(validating: rawBarcode)
        let store = ConsentBoundaryStore()
        let libraryViewModel = makeLibraryViewModel(store: store)
        let catalog = SuspendedConsentBoundaryCatalog()
        let scannerViewModel = makeScanner(catalog: catalog, library: libraryViewModel)

        await libraryViewModel.load()
        #expect(libraryViewModel.historyEnabled == false)

        scannerViewModel.acceptScan(
            ScannedBarcode(payload: rawBarcode, symbology: .retail)
        )
        try await waitUntil { await catalog.isLookupSuspended }

        await libraryViewModel.setHistoryEnabled(true)
        await catalog.resumeLookup()
        try await waitUntil {
            if case let .notFound(resolvedBarcode) = scannerViewModel.lookupState {
                return resolvedBarcode == barcode
            }
            return false
        }
        try await Task.sleep(for: .milliseconds(30))

        let recordedBarcodes = await store.recordedBarcodes
        #expect(recordedBarcodes.isEmpty)
        #expect(libraryViewModel.historyEnabled)
    }

    @Test("Disabling history before a delayed scan write revokes persistence")
    func disablingHistoryBeforeResolutionPreventsPersistence() async throws {
        let rawBarcode = "4006381333931"
        let store = ConsentBoundaryStore(initiallyEnabled: true)
        let libraryViewModel = makeLibraryViewModel(store: store)
        let catalog = SuspendedConsentBoundaryCatalog()
        let scannerViewModel = makeScanner(catalog: catalog, library: libraryViewModel)

        await libraryViewModel.load()
        #expect(libraryViewModel.historyEnabled)

        scannerViewModel.acceptScan(
            ScannedBarcode(payload: rawBarcode, symbology: .retail)
        )
        try await waitUntil { await catalog.isLookupSuspended }

        await libraryViewModel.setHistoryEnabled(false)
        await catalog.resumeLookup()
        try await waitUntil {
            if case .notFound = scannerViewModel.lookupState { return true }
            return false
        }
        try await Task.sleep(for: .milliseconds(30))

        let recordedBarcodes = await store.recordedBarcodes
        #expect(recordedBarcodes.isEmpty)
        #expect(libraryViewModel.historyEnabled == false)
    }

    @Test("Re-enabling history cannot resurrect a scan captured before revocation")
    func revokeThenReenableDoesNotResurrectPendingCameraEvent() async throws {
        let rawBarcode = "4006381333931"
        let store = ConsentBoundaryStore(initiallyEnabled: true)
        let libraryViewModel = makeLibraryViewModel(store: store)
        let catalog = SuspendedConsentBoundaryCatalog()
        let scannerViewModel = makeScanner(catalog: catalog, library: libraryViewModel)

        await libraryViewModel.load()
        let originalToken = try #require(libraryViewModel.cameraHistoryConsentToken())

        scannerViewModel.acceptScan(
            ScannedBarcode(payload: rawBarcode, symbology: .retail)
        )
        try await waitUntil { await catalog.isLookupSuspended }

        await libraryViewModel.setHistoryEnabled(false)
        #expect(libraryViewModel.cameraHistoryConsentToken() == nil)
        await libraryViewModel.setHistoryEnabled(true)
        let replacementToken = try #require(libraryViewModel.cameraHistoryConsentToken())
        #expect(replacementToken != originalToken)

        await catalog.resumeLookup()
        try await waitUntil {
            if case .notFound = scannerViewModel.lookupState { return true }
            return false
        }
        try await Task.sleep(for: .milliseconds(30))

        #expect(await store.recordedBarcodes.isEmpty)
        #expect(libraryViewModel.historyEnabled)
    }

    private func makeScanner(
        catalog: SuspendedConsentBoundaryCatalog,
        library: UserProductLibraryViewModel
    ) -> ScannerViewModel {
        ScannerViewModel(
            lookupProduct: LookupProductByBarcode(catalog: catalog),
            cameraHistoryConsentToken: { library.cameraHistoryConsentToken() },
            onCameraScanResolved: { result, token in
                library.recordCameraScan(result, consentToken: token)
            }
        )
    }

    private func makeLibraryViewModel(store: ConsentBoundaryStore) -> UserProductLibraryViewModel {
        UserProductLibraryViewModel(
            store: store,
            resolveSavedProduct: ResolveSavedProduct(
                catalog: EmptyConsentBoundaryCatalog(),
                currentCatalogVersion: "fixture-v1"
            ),
            currentCatalogVersion: "fixture-v1",
            now: { Date(timeIntervalSince1970: 1_700_000_000) }
        )
    }

    private func waitUntil(
        attempts: Int = 100,
        condition: @escaping @MainActor @Sendable () async -> Bool
    ) async throws {
        for _ in 0..<attempts {
            if await condition() { return }
            try await Task.sleep(for: .milliseconds(10))
        }
        Issue.record("Timed out waiting for consent-boundary state")
    }
}

private actor ConsentBoundaryStore: UserProductLibraryStore {
    private var enabled: Bool
    private(set) var recordedBarcodes: [Barcode] = []

    init(initiallyEnabled: Bool = false) {
        enabled = initiallyEnabled
    }

    func isHistoryEnabled() async throws -> Bool { enabled }

    func setHistoryEnabled(_ enabled: Bool) async throws {
        try Task.checkCancellation()
        self.enabled = enabled
    }

    func recordScan(
        market: CatalogMarket,
        barcode: Barcode,
        scannedAt: Date,
        catalogVersion: String,
        versionMarker: SavedProductVersionMarker
    ) async throws {
        try Task.checkCancellation()
        guard enabled else { return }
        recordedBarcodes.append(barcode)
    }

    func history(limit: Int) async throws -> [ScanHistoryEntry] { [] }
    func deleteHistoryEntry(id: Int64) async throws {}
    func clearHistory() async throws {}
    func favorites() async throws -> [FavoriteProduct] { [] }
    func favorite(for market: CatalogMarket, barcode: Barcode) async throws -> FavoriteProduct? { nil }

    func setFavorite(
        market: CatalogMarket,
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

private actor SuspendedConsentBoundaryCatalog: ProductCatalog {
    private var shouldResume = false
    private(set) var isLookupSuspended = false

    func product(for barcode: Barcode) async throws -> ProductRecord? {
        isLookupSuspended = true
        while !shouldResume {
            try await Task.sleep(for: .milliseconds(5))
        }
        isLookupSuspended = false
        return nil
    }

    func resumeLookup() {
        shouldResume = true
    }
}
