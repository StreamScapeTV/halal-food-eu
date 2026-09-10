import Foundation
import SQLite3
import Testing
@testable import HalalFoodEU

@Suite("Legacy user-library migration diagnostics")
struct UserProductLibraryMigrationDiagnosticsTests {
    @Test("Germany-only v1 store migrates through the production actor")
    func migrationThroughProductionActor() async throws {
        let directoryURL = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: directoryURL, withIntermediateDirectories: true)
        let databaseURL = directoryURL.appendingPathComponent("legacy-user-library.sqlite3")
        try createLegacyStore(at: databaseURL)

        let store = SQLiteUserProductLibrary(databaseURL: databaseURL)
        do {
            let entries = try await store.history(limit: 10)
            let favorites = try await store.favorites()
            diagnostic(
                "HFEU_V1_MIGRATION_RESULT history=\(entries.count) " +
                "historyMarkets=\(entries.map(\.market.rawValue)) favorites=\(favorites.count) " +
                "favoriteMarkets=\(favorites.map(\.market.rawValue))"
            )

            #expect(entries.count == 1)
            #expect(entries.first?.market == .germany)
            #expect(entries.first?.barcode.rawValue == "0200000000004")
            #expect(favorites.count == 1)
            #expect(favorites.first?.market == .germany)
            #expect(favorites.first?.barcode.rawValue == "0200000000004")
        } catch {
            diagnostic(
                "HFEU_V1_MIGRATION_ERROR type=\(String(reflecting: type(of: error))) " +
                "description=\(error.localizedDescription) raw=\(String(reflecting: error))"
            )
            throw error
        }
    }

    private func createLegacyStore(at url: URL) throws {
        var database: OpaquePointer?
        let openResult = sqlite3_open_v2(
            url.path,
            &database,
            SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX,
            nil
        )
        guard openResult == SQLITE_OK, let database else {
            if let database { sqlite3_close(database) }
            throw CocoaError(.fileWriteUnknown)
        }

        let marker = #"{"fingerprintSchemaVersion":1,"wasPresent":false}"#
        let sql = """
        PRAGMA application_id = \(SQLiteUserProductLibrary.expectedApplicationID);
        PRAGMA user_version = 1;
        CREATE TABLE user_settings(key TEXT PRIMARY KEY, integer_value INTEGER NOT NULL CHECK(integer_value IN (0, 1)));
        INSERT INTO user_settings(key, integer_value) VALUES ('history_enabled', 1);
        CREATE TABLE scan_history(id INTEGER PRIMARY KEY AUTOINCREMENT, gtin TEXT NOT NULL, scanned_at TEXT NOT NULL, catalog_version TEXT NOT NULL, version_marker_json TEXT NOT NULL);
        CREATE INDEX idx_scan_history_scanned_at ON scan_history(scanned_at DESC, id DESC);
        CREATE TABLE favorites(gtin TEXT PRIMARY KEY, saved_at TEXT NOT NULL, catalog_version TEXT NOT NULL, version_marker_json TEXT NOT NULL);
        CREATE INDEX idx_favorites_saved_at ON favorites(saved_at DESC, gtin ASC);
        INSERT INTO scan_history(gtin, scanned_at, catalog_version, version_marker_json) VALUES ('0200000000004', '2023-11-14T22:13:20Z', 'fixture-v1', '\(marker)');
        INSERT INTO favorites(gtin, saved_at, catalog_version, version_marker_json) VALUES ('0200000000004', '2023-11-14T22:13:20Z', 'fixture-v1', '\(marker)');
        """

        var errorMessage: UnsafeMutablePointer<CChar>?
        let executeResult = sqlite3_exec(database, sql, nil, nil, &errorMessage)
        if executeResult != SQLITE_OK {
            let message = errorMessage.map { String(cString: $0) } ?? String(cString: sqlite3_errmsg(database))
            if let errorMessage { sqlite3_free(errorMessage) }
            sqlite3_close(database)
            diagnostic("HFEU_V1_FIXTURE_ERROR code=\(executeResult) message=\(message)")
            throw CocoaError(.fileWriteUnknown)
        }

        let closeResult = sqlite3_close(database)
        guard closeResult == SQLITE_OK else {
            diagnostic("HFEU_V1_FIXTURE_CLOSE_ERROR code=\(closeResult)")
            throw CocoaError(.fileWriteUnknown)
        }
    }

    private func diagnostic(_ message: String) {
        FileHandle.standardError.write(Data((message + "\n").utf8))
    }
}
