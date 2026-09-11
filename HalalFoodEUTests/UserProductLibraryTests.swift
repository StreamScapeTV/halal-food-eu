import Foundation
import Testing
import SQLite3
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
            market: .germany,
            barcode: barcode,
            scannedAt: firstDate,
            catalogVersion: "fixture-v1",
            versionMarker: marker
        )
        let initialHistory = try await store.history(limit: 10)
        #expect(initialHistory.isEmpty)

        try await store.setHistoryEnabled(true)
        try await store.recordScan(
            market: .germany,
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
                market: .germany,
                barcode: barcode,
                scannedAt: date,
                catalogVersion: product.catalogVersion,
                versionMarker: marker
            )
            try await store.setFavorite(
                market: .germany,
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
        let favorite = try await reopened.favorite(for: .germany, barcode: barcode)
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
                market: .germany,
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
            market: .germany,
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
            market: .germany,
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
                market: .germany,
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

    @Test("Germany-only schema v1 migrates saved references to DE")
    func migratesGermanyOnlyV1Store() async throws {
        let fixture = try TemporaryUserLibraryFixture()
        try createLegacyV1Store(at: fixture.databaseURL)
        let store = SQLiteUserProductLibrary(databaseURL: fixture.databaseURL)
        let expectedBarcode = try Barcode(validating: "0200000000004")

        let entries = try await store.history(limit: 10)
        let favorites = try await store.favorites()

        #expect(entries.count == 1)
        #expect(entries.first?.market == .germany)
        #expect(entries.first?.barcode == expectedBarcode)
        #expect(favorites.count == 1)
        #expect(favorites.first?.market == .germany)
        #expect(favorites.first?.barcode == expectedBarcode)
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

    private func createLegacyV1Store(at url: URL) throws {
        var database: OpaquePointer?
        guard sqlite3_open_v2(
            url.path,
            &database,
            SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX,
            nil
        ) == SQLITE_OK, let database else {
            throw CocoaError(.fileWriteUnknown)
        }
        defer { sqlite3_close(database) }

        let marker = try Self.markerJSON(SavedProductVersionMarker(product: nil))
        let scannedAt = "2023-11-14T22:13:20Z"
        let sql = """
        PRAGMA application_id = \(SQLiteUserProductLibrary.expectedApplicationID);
        PRAGMA user_version = 1;
        CREATE TABLE user_settings(key TEXT PRIMARY KEY, integer_value INTEGER NOT NULL CHECK(integer_value IN (0, 1)));
        INSERT INTO user_settings(key, integer_value) VALUES ('history_enabled', 1);
        CREATE TABLE scan_history(id INTEGER PRIMARY KEY AUTOINCREMENT, gtin TEXT NOT NULL, scanned_at TEXT NOT NULL, catalog_version TEXT NOT NULL, version_marker_json TEXT NOT NULL);
        CREATE INDEX idx_scan_history_scanned_at ON scan_history(scanned_at DESC, id DESC);
        CREATE TABLE favorites(gtin TEXT PRIMARY KEY, saved_at TEXT NOT NULL, catalog_version TEXT NOT NULL, version_marker_json TEXT NOT NULL);
        CREATE INDEX idx_favorites_saved_at ON favorites(saved_at DESC, gtin ASC);
        INSERT INTO scan_history(gtin, scanned_at, catalog_version, version_marker_json) VALUES ('0200000000004', '\(scannedAt)', 'fixture-v1', '\(marker.replacingOccurrences(of: "'", with: "''"))');
        INSERT INTO favorites(gtin, saved_at, catalog_version, version_marker_json) VALUES ('0200000000004', '\(scannedAt)', 'fixture-v1', '\(marker.replacingOccurrences(of: "'", with: "''"))');
        """
        guard sqlite3_exec(database, sql, nil, nil, nil) == SQLITE_OK else {
            throw CocoaError(.fileWriteUnknown)
        }
    }

    private static func markerJSON(_ marker: SavedProductVersionMarker) throws -> String {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        encoder.dateEncodingStrategy = .iso8601
        return try String(decoding: encoder.encode(marker), as: UTF8.self)
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
