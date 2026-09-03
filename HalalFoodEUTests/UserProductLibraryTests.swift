import Foundation
import Testing
@testable import HalalFoodEU

@Suite("Local scan history and favorites")
struct UserProductLibraryTests {
    @Test("History is default-off and persists only after explicit opt-in")
    func historyOptIn() async throws {
        let fixture = try TemporaryUserLibraryFixture()
        let store = SQLiteUserProductLibrary(databaseURL: fixture.databaseURL)
        let barcode = try Barcode(validating: "0200000000004")
        let marker = SavedProductVersionMarker(product: nil)
        let firstDate = Date(timeIntervalSince1970: 1_700_000_000)

        let initiallyEnabled = try await store.isHistoryEnabled()
        #expect(initiallyEnabled == false)
        try await store.recordScan(
            barcode: barcode,
            scannedAt: firstDate,
            catalogVersion: "fixture-v1",
            versionMarker: marker
        )
        let initialHistory = try await store.history(limit: 10)
        #expect(initialHistory.isEmpty)

        try await store.setHistoryEnabled(true)
        try await store.recordScan(
            barcode: barcode,
            scannedAt: firstDate,
            catalogVersion: "fixture-v1",
            versionMarker: marker
        )
        let entries = try await store.history(limit: 10)
        #expect(entries.count == 1)
        #expect(entries.first?.barcode == barcode)
        #expect(entries.first?.catalogVersion == "fixture-v1")
        #expect(entries.first?.versionMarker == marker)
    }

    @Test("History and favorites persist across store reopen")
    func persistenceReopen() async throws {
        let fixture = try TemporaryUserLibraryFixture()
        let barcode = try Barcode(validating: "0200000000004")
        let product = makeProduct(barcode: barcode, name: "Fixture Oat Drink")
        let marker = SavedProductVersionMarker(product: product)
        let date = Date(timeIntervalSince1970: 1_700_000_100)

        do {
            let store = SQLiteUserProductLibrary(databaseURL: fixture.databaseURL)
            try await store.setHistoryEnabled(true)
            try await store.recordScan(
                barcode: barcode,
                scannedAt: date,
                catalogVersion: product.catalogVersion,
                versionMarker: marker
            )
            try await store.setFavorite(
                barcode: barcode,
                savedAt: date,
                catalogVersion: product.catalogVersion,
                versionMarker: marker,
                isFavorite: true
            )
        }

        let reopened = SQLiteUserProductLibrary(databaseURL: fixture.databaseURL)
        let reopenedEnabled = try await reopened.isHistoryEnabled()
        #expect(reopenedEnabled)
        let reopenedHistory = try await reopened.history(limit: 10)
        #expect(reopenedHistory.count == 1)
        let favorite = try await reopened.favorite(for: barcode)
        #expect(favorite?.barcode == barcode)
        #expect(favorite?.versionMarker == marker)
    }

    @Test("History supports per-entry deletion and clear-all")
    func deletionAndClear() async throws {
        let fixture = try TemporaryUserLibraryFixture()
        let store = SQLiteUserProductLibrary(databaseURL: fixture.databaseURL)
        try await store.setHistoryEnabled(true)

        for (index, raw) in ["0200000000004", "0200000000011"].enumerated() {
            let barcode = try Barcode(validating: raw)
            try await store.recordScan(
                barcode: barcode,
                scannedAt: Date(timeIntervalSince1970: 1_700_001_000 + Double(index)),
                catalogVersion: "fixture-v1",
                versionMarker: SavedProductVersionMarker(product: nil)
            )
        }

        var entries = try await store.history(limit: 10)
        #expect(entries.count == 2)
        try await store.deleteHistoryEntry(id: try #require(entries.first?.id))
        entries = try await store.history(limit: 10)
        #expect(entries.count == 1)
        try await store.clearHistory()
        let clearedHistory = try await store.history(limit: 10)
        #expect(clearedHistory.isEmpty)
    }

    @Test("Favorites are explicit and independent from history opt-in")
    func favoritesAreIndependent() async throws {
        let fixture = try TemporaryUserLibraryFixture()
        let store = SQLiteUserProductLibrary(databaseURL: fixture.databaseURL)
        let barcode = try Barcode(validating: "0200000000004")
        let product = makeProduct(barcode: barcode, name: "Fixture Oat Drink")
        let marker = SavedProductVersionMarker(product: product)

        let historyInitiallyDisabled = try await store.isHistoryEnabled()
        #expect(historyInitiallyDisabled == false)
        try await store.setFavorite(
            barcode: barcode,
            savedAt: Date(timeIntervalSince1970: 1_700_002_000),
            catalogVersion: product.catalogVersion,
            versionMarker: marker,
            isFavorite: true
        )
        let savedFavorites = try await store.favorites()
        #expect(savedFavorites.map(\.barcode) == [barcode])
        let historyStillDisabled = try await store.isHistoryEnabled()
        #expect(historyStillDisabled == false)

        try await store.setFavorite(
            barcode: barcode,
            savedAt: Date(),
            catalogVersion: product.catalogVersion,
            versionMarker: marker,
            isFavorite: false
        )
        let removedFavorites = try await store.favorites()
        #expect(removedFavorites.isEmpty)
    }

    @Test("History retention is bounded to the newest 200 scans")
    func boundedHistory() async throws {
        let fixture = try TemporaryUserLibraryFixture()
        let store = SQLiteUserProductLibrary(databaseURL: fixture.databaseURL)
        let barcode = try Barcode(validating: "0200000000004")
        try await store.setHistoryEnabled(true)

        for index in 0..<205 {
            try await store.recordScan(
                barcode: barcode,
                scannedAt: Date(timeIntervalSince1970: 1_700_010_000 + Double(index)),
                catalogVersion: "fixture-v1",
                versionMarker: SavedProductVersionMarker(product: nil)
            )
        }

        let entries = try await store.history(limit: UserProductLibraryPolicy.maximumHistoryEntries)
        #expect(entries.count == UserProductLibraryPolicy.maximumHistoryEntries)
        #expect(entries.first?.scannedAt == Date(timeIntervalSince1970: 1_700_010_204))
        #expect(entries.last?.scannedAt == Date(timeIntervalSince1970: 1_700_010_005))
    }

    @Test("Version markers detect material product changes without global-version false positives")
    func versionComparison() throws {
        let barcode = try Barcode(validating: "0200000000004")
        let original = makeProduct(barcode: barcode, name: "Fixture Oat Drink", catalogVersion: "v1")
        let sameRecordNewCatalog = makeProduct(barcode: barcode, name: "Fixture Oat Drink", catalogVersion: "v2")
        let changed = makeProduct(barcode: barcode, name: "Changed Oat Drink", catalogVersion: "v2")
        let marker = SavedProductVersionMarker(product: original)

        #expect(marker.recordFingerprint?.count == 64)
        #expect(marker.comparison(with: sameRecordNewCatalog) == .unchanged)
        #expect(marker.comparison(with: changed) == .changed)
        #expect(marker.comparison(with: nil) == .noLongerPresent)
        #expect(SavedProductVersionMarker(product: nil).comparison(with: original) == .nowAvailable)
    }

    @Test("A non-SQLite local store fails closed")
    func corruptStoreFailsClosed() async throws {
        let fixture = try TemporaryUserLibraryFixture()
        try Data("not-a-sqlite-database".utf8).write(to: fixture.databaseURL)
        let store = SQLiteUserProductLibrary(databaseURL: fixture.databaseURL)

        do {
            _ = try await store.isHistoryEnabled()
            Issue.record("Expected corrupt local store to fail closed")
        } catch {
            // Expected: corrupt local user data must never be accepted as a valid store.
        }
    }

    private func makeProduct(
        barcode: Barcode,
        name: String,
        catalogVersion: String = "fixture-v1"
    ) -> ProductRecord {
        ProductRecord(
            barcode: barcode,
            name: name,
            brand: "Fixture Brand",
            observation: nil,
            assessment: .unreviewedUnknown,
            catalogVersion: catalogVersion,
            details: ProductRecordDetails(
                market: "DE",
                brandOwner: nil,
                quantity: nil,
                conflictFlags: [],
                retailerEvidence: [],
                remoteImages: []
            )
        )
    }
}

private struct TemporaryUserLibraryFixture {
    let directoryURL: URL
    let databaseURL: URL

    init() throws {
        directoryURL = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: directoryURL, withIntermediateDirectories: true)
        databaseURL = directoryURL.appendingPathComponent("user-library.sqlite3")
    }
}
