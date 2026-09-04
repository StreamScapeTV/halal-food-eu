import Foundation
import Testing
@testable import HalalFoodEU

@Suite("Local history and favorites UI state")
@MainActor
struct UserProductLibraryViewModelTests {
    @Test("Load publishes opt-in, history, and favorites together")
    func load() async throws {
        let barcode = try Barcode(validating: "0200000000004")
        let marker = SavedProductVersionMarker(product: nil)
        let history = ScanHistoryEntry(
            id: 7,
            barcode: barcode,
            scannedAt: Date(timeIntervalSince1970: 1_700_000_000),
            catalogVersion: "fixture-v1",
            versionMarker: marker
        )
        let favorite = FavoriteProduct(
            barcode: barcode,
            savedAt: Date(timeIntervalSince1970: 1_700_000_100),
            catalogVersion: "fixture-v1",
            versionMarker: marker
        )
        let store = UserLibraryTestStore(
            historyEnabled: true,
            history: [history],
            favorites: [favorite]
        )
        let viewModel = makeViewModel(store: store)

        await viewModel.load()

        #expect(viewModel.historyEnabled)
        #expect(viewModel.history == [history])
        #expect(viewModel.favorites == [favorite])
        #expect(viewModel.errorMessage == nil)
    }

    @Test("Failed history opt-in rolls the switch back")
    func optInFailureRollsBack() async {
        let store = UserLibraryTestStore(failHistorySetting: true)
        let viewModel = makeViewModel(store: store)

        await viewModel.setHistoryEnabled(true)

        #expect(viewModel.historyEnabled == false)
        #expect(viewModel.errorMessage?.contains("fixture setting failure") == true)
    }

    @Test("Resolved camera scan writes canonical local history data")
    func recordCameraScan() async throws {
        let barcode = try Barcode(validating: "4006381333931")
        let store = UserLibraryTestStore(historyEnabled: true)
        let timestamp = Date(timeIntervalSince1970: 1_700_010_000)
        let viewModel = makeViewModel(
            store: store,
            currentCatalogVersion: "catalog-v2",
            now: { timestamp }
        )
        await viewModel.load()

        viewModel.recordCameraScan(ProductLookupResult(barcode: barcode, product: nil))
        try await waitUntil {
            await store.recordedScans.count == 1
        }

        let scans = await store.recordedScans
        #expect(scans.count == 1)
        #expect(scans[0].barcode == barcode)
        #expect(scans[0].scannedAt == timestamp)
        #expect(scans[0].catalogVersion == "catalog-v2")
        #expect(scans[0].versionMarker.wasPresent == false)
    }

    @Test("Favorites work while scan history remains disabled")
    func favoriteIndependentFromHistory() async throws {
        let barcode = try Barcode(validating: "0200000000004")
        let product = makeProduct(barcode: barcode)
        let store = UserLibraryTestStore(historyEnabled: false)
        let timestamp = Date(timeIntervalSince1970: 1_700_020_000)
        let viewModel = makeViewModel(store: store, now: { timestamp })
        await viewModel.load()

        await viewModel.toggleFavorite(product)

        #expect(viewModel.historyEnabled == false)
        #expect(viewModel.isFavorite(barcode))
        let enabled = await store.historyEnabledValue
        #expect(enabled == false)
    }

    @Test("Delete and clear update published history")
    func deleteAndClear() async throws {
        let barcode = try Barcode(validating: "0200000000004")
        let marker = SavedProductVersionMarker(product: nil)
        let first = ScanHistoryEntry(
            id: 1,
            barcode: barcode,
            scannedAt: Date(timeIntervalSince1970: 1_700_030_000),
            catalogVersion: "fixture-v1",
            versionMarker: marker
        )
        let second = ScanHistoryEntry(
            id: 2,
            barcode: barcode,
            scannedAt: Date(timeIntervalSince1970: 1_700_030_001),
            catalogVersion: "fixture-v1",
            versionMarker: marker
        )
        let store = UserLibraryTestStore(historyEnabled: true, history: [second, first])
        let viewModel = makeViewModel(store: store)
        await viewModel.load()

        await viewModel.deleteHistoryEntry(second)
        #expect(viewModel.history == [first])

        await viewModel.clearHistory()
        #expect(viewModel.history.isEmpty)
    }

    private func makeViewModel(
        store: UserLibraryTestStore,
        currentCatalogVersion: String = "fixture-v1",
        now: @escaping @Sendable () -> Date = { Date(timeIntervalSince1970: 1_700_000_000) }
    ) -> UserProductLibraryViewModel {
        UserProductLibraryViewModel(
            store: store,
            resolveSavedProduct: ResolveSavedProduct(
                catalog: EmptySavedProductCatalog(),
                currentCatalogVersion: currentCatalogVersion
            ),
            currentCatalogVersion: currentCatalogVersion,
            now: now
        )
    }

    private func waitUntil(
        attempts: Int = 100,
        condition: @escaping @Sendable () async -> Bool
    ) async throws {
        for _ in 0..<attempts {
            if await condition() { return }
            try await Task.sleep(for: .milliseconds(10))
        }
        Issue.record("Timed out waiting for local user-library state")
    }

    private func makeProduct(barcode: Barcode) -> ProductRecord {
        ProductRecord(
            barcode: barcode,
            name: "Fixture Oat Drink",
            brand: "Fixture Brand",
            observation: nil,
            assessment: .unreviewedUnknown,
            catalogVersion: "fixture-v1",
            details: ProductRecordDetails(
                market: "DE",
                brandOwner: nil,
                quantity: "1 L",
                conflictFlags: [],
                retailerEvidence: [],
                remoteImages: []
            )
        )
    }
}

private struct RecordedUserScan: Sendable {
    let barcode: Barcode
    let scannedAt: Date
    let catalogVersion: String
    let versionMarker: SavedProductVersionMarker
}

private actor UserLibraryTestStore: UserProductLibraryStore {
    private var historyEnabled: Bool
    private var historyEntries: [ScanHistoryEntry]
    private var favoriteEntries: [FavoriteProduct]
    private let failHistorySetting: Bool
    private(set) var recordedScans: [RecordedUserScan] = []

    init(
        historyEnabled: Bool = false,
        history: [ScanHistoryEntry] = [],
        favorites: [FavoriteProduct] = [],
        failHistorySetting: Bool = false
    ) {
        self.historyEnabled = historyEnabled
        historyEntries = history
        favoriteEntries = favorites
        self.failHistorySetting = failHistorySetting
    }

    var historyEnabledValue: Bool { historyEnabled }

    func isHistoryEnabled() async throws -> Bool { historyEnabled }

    func setHistoryEnabled(_ enabled: Bool) async throws {
        if failHistorySetting {
            throw UserProductLibraryError.queryFailed("fixture setting failure")
        }
        historyEnabled = enabled
    }

    func recordScan(
        barcode: Barcode,
        scannedAt: Date,
        catalogVersion: String,
        versionMarker: SavedProductVersionMarker
    ) async throws {
        guard historyEnabled else { return }
        recordedScans.append(
            RecordedUserScan(
                barcode: barcode,
                scannedAt: scannedAt,
                catalogVersion: catalogVersion,
                versionMarker: versionMarker
            )
        )
        historyEntries.insert(
            ScanHistoryEntry(
                id: Int64(historyEntries.count + 1),
                barcode: barcode,
                scannedAt: scannedAt,
                catalogVersion: catalogVersion,
                versionMarker: versionMarker
            ),
            at: 0
        )
    }

    func history(limit: Int) async throws -> [ScanHistoryEntry] {
        Array(historyEntries.prefix(limit))
    }

    func deleteHistoryEntry(id: Int64) async throws {
        historyEntries.removeAll { $0.id == id }
    }

    func clearHistory() async throws {
        historyEntries = []
    }

    func favorites() async throws -> [FavoriteProduct] { favoriteEntries }

    func favorite(for barcode: Barcode) async throws -> FavoriteProduct? {
        favoriteEntries.first { $0.barcode == barcode }
    }

    func setFavorite(
        barcode: Barcode,
        savedAt: Date,
        catalogVersion: String,
        versionMarker: SavedProductVersionMarker,
        isFavorite: Bool
    ) async throws {
        favoriteEntries.removeAll { $0.barcode == barcode }
        if isFavorite {
            favoriteEntries.append(
                FavoriteProduct(
                    barcode: barcode,
                    savedAt: savedAt,
                    catalogVersion: catalogVersion,
                    versionMarker: versionMarker
                )
            )
        }
    }
}

private actor EmptySavedProductCatalog: ProductCatalog {
    func product(for barcode: Barcode) async throws -> ProductRecord? { nil }
}
