import Foundation
import SQLite3
import Testing
@testable import HalalFoodEU

@Suite("Legacy user-library migration diagnostics")
struct UserProductLibraryMigrationDiagnosticsTests {
    @Test("Stage 1: production actor opens and preserves history opt-in")
    func actorOpensMigratedStore() async throws {
        let fixture = try makeLegacyFixture()
        let store = SQLiteUserProductLibrary(databaseURL: fixture)
        #expect(try await store.isHistoryEnabled())
    }

    @Test("Stage 2: production actor decodes migrated history")
    func actorDecodesMigratedHistory() async throws {
        let fixture = try makeLegacyFixture()
        let store = SQLiteUserProductLibrary(databaseURL: fixture)
        let entries = try await store.history(limit: 10)

        #expect(entries.count == 1)
        #expect(entries.first?.market == .germany)
        #expect(entries.first?.barcode.rawValue == "0200000000004")
    }

    @Test("Stage 3: production actor decodes migrated favorites")
    func actorDecodesMigratedFavorites() async throws {
        let fixture = try makeLegacyFixture()
        let store = SQLiteUserProductLibrary(databaseURL: fixture)
        let favorites = try await store.favorites()

        #expect(favorites.count == 1)
        #expect(favorites.first?.market == .germany)
        #expect(favorites.first?.barcode.rawValue == "0200000000004")
    }

    @Test("Stage 4: raw SQLite migration shape is readable after actor opens")
    func rawShapeAfterActorMigration() async throws {
        let fixture = try makeLegacyFixture()
        let store = SQLiteUserProductLibrary(databaseURL: fixture)
        #expect(try await store.isHistoryEnabled())

        var database: OpaquePointer?
        let openResult = sqlite3_open_v2(
            fixture.path,
            &database,
            SQLITE_OPEN_READONLY | SQLITE_OPEN_FULLMUTEX,
            nil
        )
        guard openResult == SQLITE_OK, let database else {
            if let database { sqlite3_close(database) }
            throw CocoaError(.fileReadUnknown)
        }
        defer { sqlite3_close(database) }

        #expect(try pragma("user_version", database: database) == SQLiteUserProductLibrary.supportedSchemaVersion)
        #expect(try scalar("SELECT COUNT(*) FROM scan_history WHERE market = 'DE';", database: database) == 1)
        #expect(try scalar("SELECT COUNT(*) FROM favorites WHERE market = 'DE';", database: database) == 1)
    }

    private func makeLegacyFixture() throws -> URL {
        let directoryURL = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: directoryURL, withIntermediateDirectories: true)
        let databaseURL = directoryURL.appendingPathComponent("legacy-user-library.sqlite3")
        try createLegacyStore(at: databaseURL)
        return databaseURL
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
            if let errorMessage { sqlite3_free(errorMessage) }
            sqlite3_close(database)
            throw CocoaError(.fileWriteUnknown)
        }

        guard sqlite3_close(database) == SQLITE_OK else {
            throw CocoaError(.fileWriteUnknown)
        }
    }

    private func pragma(_ name: String, database: OpaquePointer) throws -> Int32 {
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(database, "PRAGMA \(name);", -1, &statement, nil) == SQLITE_OK, let statement else {
            throw CocoaError(.fileReadUnknown)
        }
        defer { sqlite3_finalize(statement) }
        guard sqlite3_step(statement) == SQLITE_ROW else { throw CocoaError(.fileReadUnknown) }
        return sqlite3_column_int(statement, 0)
    }

    private func scalar(_ sql: String, database: OpaquePointer) throws -> Int32 {
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(database, sql, -1, &statement, nil) == SQLITE_OK, let statement else {
            throw CocoaError(.fileReadUnknown)
        }
        defer { sqlite3_finalize(statement) }
        guard sqlite3_step(statement) == SQLITE_ROW else { throw CocoaError(.fileReadUnknown) }
        return sqlite3_column_int(statement, 0)
    }
}
