import Foundation
import SQLite3

private final class UserLibrarySQLiteConnection: @unchecked Sendable {
    let handle: OpaquePointer
    init(handle: OpaquePointer) { self.handle = handle }
    deinit { sqlite3_close(handle) }
}

actor SQLiteUserProductLibrary: UserProductLibraryStore {
    static let supportedSchemaVersion = 2
    static let legacyGermanyOnlySchemaVersion = 1
    static let expectedApplicationID: Int32 = 1_212_568_900 // ASCII "HFUD"

    private let databaseURL: URL
    private var connection: UserLibrarySQLiteConnection?

    init(databaseURL: URL) { self.databaseURL = databaseURL }

    func isHistoryEnabled() async throws -> Bool {
        try Task.checkCancellation()
        return try historyEnabled(connection: openIfNeeded())
    }

    func setHistoryEnabled(_ enabled: Bool) async throws {
        try Task.checkCancellation()
        let connection = try openIfNeeded()
        let statement = try prepare("UPDATE user_settings SET integer_value = ? WHERE key = 'history_enabled';", connection: connection)
        defer { sqlite3_finalize(statement) }
        guard sqlite3_bind_int(statement, 1, enabled ? 1 : 0) == SQLITE_OK,
              sqlite3_step(statement) == SQLITE_DONE else { throw queryError(connection: connection) }
        guard sqlite3_changes(connection.handle) == 1 else {
            throw UserProductLibraryError.invalidRecord("history opt-in setting is missing")
        }
    }

    func recordScan(
        market: CatalogMarket,
        barcode: Barcode,
        scannedAt: Date,
        catalogVersion: String,
        versionMarker: SavedProductVersionMarker
    ) async throws {
        guard !catalogVersion.isEmpty else {
            throw UserProductLibraryError.invalidRecord("scan history catalog version is empty")
        }
        try Task.checkCancellation()
        let connection = try openIfNeeded()
        // This is deliberately checked immediately before the write transaction so
        // a cancelled/revoked camera event cannot race an earlier optimistic state.
        guard try historyEnabled(connection: connection) else { return }
        try Task.checkCancellation()

        try execute("BEGIN IMMEDIATE;", connection: connection)
        do {
            // Re-read persisted consent after acquiring the writer lock.
            guard try historyEnabled(connection: connection) else {
                try execute("ROLLBACK;", connection: connection)
                return
            }
            let statement = try prepare(
                """
                INSERT INTO scan_history(market, gtin, scanned_at, catalog_version, version_marker_json)
                VALUES (?, ?, ?, ?, ?);
                """,
                connection: connection
            )
            defer { sqlite3_finalize(statement) }
            try bind(market.rawValue, at: 1, to: statement, connection: connection)
            try bind(barcode.rawValue, at: 2, to: statement, connection: connection)
            try bind(Self.dateString(scannedAt), at: 3, to: statement, connection: connection)
            try bind(catalogVersion, at: 4, to: statement, connection: connection)
            try bind(try Self.markerJSON(versionMarker), at: 5, to: statement, connection: connection)
            guard sqlite3_step(statement) == SQLITE_DONE else { throw queryError(connection: connection) }

            let trim = try prepare(
                """
                DELETE FROM scan_history
                WHERE id IN (
                    SELECT id FROM scan_history
                    ORDER BY scanned_at DESC, id DESC
                    LIMIT -1 OFFSET ?
                );
                """,
                connection: connection
            )
            defer { sqlite3_finalize(trim) }
            guard sqlite3_bind_int(trim, 1, Int32(UserProductLibraryPolicy.maximumHistoryEntries)) == SQLITE_OK,
                  sqlite3_step(trim) == SQLITE_DONE else { throw queryError(connection: connection) }
            try execute("COMMIT;", connection: connection)
        } catch {
            try? execute("ROLLBACK;", connection: connection)
            throw error
        }
    }

    func history(limit: Int = UserProductLibraryPolicy.maximumHistoryEntries) async throws -> [ScanHistoryEntry] {
        guard (1...UserProductLibraryPolicy.maximumHistoryEntries).contains(limit) else {
            throw UserProductLibraryError.invalidRecord("history page limit is outside supported bounds")
        }
        try Task.checkCancellation()
        let connection = try openIfNeeded()
        let statement = try prepare(
            """
            SELECT id, market, gtin, scanned_at, catalog_version, version_marker_json
            FROM scan_history
            ORDER BY scanned_at DESC, id DESC
            LIMIT ?;
            """,
            connection: connection
        )
        defer { sqlite3_finalize(statement) }
        guard sqlite3_bind_int(statement, 1, Int32(limit)) == SQLITE_OK else { throw queryError(connection: connection) }

        var entries: [ScanHistoryEntry] = []
        while true {
            try Task.checkCancellation()
            switch sqlite3_step(statement) {
            case SQLITE_ROW:
                guard let market = CatalogMarket(rawValue: requiredText(statement, column: 1)) else {
                    throw UserProductLibraryError.invalidRecord("history entry has an invalid market")
                }
                let barcode = try Barcode(validating: requiredText(statement, column: 2))
                let scannedAt = try Self.parseDate(requiredText(statement, column: 3))
                let catalogVersion = requiredText(statement, column: 4)
                guard !catalogVersion.isEmpty else { throw UserProductLibraryError.invalidRecord("history entry has an empty catalog version") }
                entries.append(ScanHistoryEntry(
                    id: sqlite3_column_int64(statement, 0), market: market, barcode: barcode,
                    scannedAt: scannedAt, catalogVersion: catalogVersion,
                    versionMarker: try Self.decodeMarker(requiredText(statement, column: 5))
                ))
            case SQLITE_DONE: return entries
            default: throw queryError(connection: connection)
            }
        }
    }

    func deleteHistoryEntry(id: Int64) async throws {
        try Task.checkCancellation()
        let connection = try openIfNeeded()
        let statement = try prepare("DELETE FROM scan_history WHERE id = ?;", connection: connection)
        defer { sqlite3_finalize(statement) }
        guard sqlite3_bind_int64(statement, 1, id) == SQLITE_OK, sqlite3_step(statement) == SQLITE_DONE else {
            throw queryError(connection: connection)
        }
    }

    func clearHistory() async throws {
        try Task.checkCancellation()
        try execute("DELETE FROM scan_history;", connection: openIfNeeded())
    }

    func favorites() async throws -> [FavoriteProduct] {
        try Task.checkCancellation()
        let connection = try openIfNeeded()
        let statement = try prepare(
            """
            SELECT market, gtin, saved_at, catalog_version, version_marker_json
            FROM favorites
            ORDER BY saved_at DESC, market ASC, gtin ASC;
            """,
            connection: connection
        )
        defer { sqlite3_finalize(statement) }
        var result: [FavoriteProduct] = []
        while true {
            try Task.checkCancellation()
            switch sqlite3_step(statement) {
            case SQLITE_ROW:
                guard let market = CatalogMarket(rawValue: requiredText(statement, column: 0)) else {
                    throw UserProductLibraryError.invalidRecord("favorite has an invalid market")
                }
                let barcode = try Barcode(validating: requiredText(statement, column: 1))
                let catalogVersion = requiredText(statement, column: 3)
                guard !catalogVersion.isEmpty else { throw UserProductLibraryError.invalidRecord("favorite has an empty catalog version") }
                result.append(FavoriteProduct(
                    market: market,
                    barcode: barcode,
                    savedAt: try Self.parseDate(requiredText(statement, column: 2)),
                    catalogVersion: catalogVersion,
                    versionMarker: try Self.decodeMarker(requiredText(statement, column: 4))
                ))
            case SQLITE_DONE: return result
            default: throw queryError(connection: connection)
            }
        }
    }

    func favorite(for market: CatalogMarket, barcode: Barcode) async throws -> FavoriteProduct? {
        try Task.checkCancellation()
        let connection = try openIfNeeded()
        let statement = try prepare(
            "SELECT saved_at, catalog_version, version_marker_json FROM favorites WHERE market = ? AND gtin = ? LIMIT 1;",
            connection: connection
        )
        defer { sqlite3_finalize(statement) }
        try bind(market.rawValue, at: 1, to: statement, connection: connection)
        try bind(barcode.rawValue, at: 2, to: statement, connection: connection)
        switch sqlite3_step(statement) {
        case SQLITE_ROW:
            let catalogVersion = requiredText(statement, column: 1)
            guard !catalogVersion.isEmpty else { throw UserProductLibraryError.invalidRecord("favorite has an empty catalog version") }
            return FavoriteProduct(
                market: market, barcode: barcode,
                savedAt: try Self.parseDate(requiredText(statement, column: 0)),
                catalogVersion: catalogVersion,
                versionMarker: try Self.decodeMarker(requiredText(statement, column: 2))
            )
        case SQLITE_DONE: return nil
        default: throw queryError(connection: connection)
        }
    }

    func setFavorite(
        market: CatalogMarket,
        barcode: Barcode,
        savedAt: Date,
        catalogVersion: String,
        versionMarker: SavedProductVersionMarker,
        isFavorite: Bool
    ) async throws {
        guard !catalogVersion.isEmpty else { throw UserProductLibraryError.invalidRecord("favorite catalog version is empty") }
        try Task.checkCancellation()
        let connection = try openIfNeeded()
        if isFavorite {
            let statement = try prepare(
                """
                INSERT INTO favorites(market, gtin, saved_at, catalog_version, version_marker_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(market, gtin) DO UPDATE SET
                    saved_at = excluded.saved_at,
                    catalog_version = excluded.catalog_version,
                    version_marker_json = excluded.version_marker_json;
                """,
                connection: connection
            )
            defer { sqlite3_finalize(statement) }
            try bind(market.rawValue, at: 1, to: statement, connection: connection)
            try bind(barcode.rawValue, at: 2, to: statement, connection: connection)
            try bind(Self.dateString(savedAt), at: 3, to: statement, connection: connection)
            try bind(catalogVersion, at: 4, to: statement, connection: connection)
            try bind(try Self.markerJSON(versionMarker), at: 5, to: statement, connection: connection)
            guard sqlite3_step(statement) == SQLITE_DONE else { throw queryError(connection: connection) }
        } else {
            let statement = try prepare("DELETE FROM favorites WHERE market = ? AND gtin = ?;", connection: connection)
            defer { sqlite3_finalize(statement) }
            try bind(market.rawValue, at: 1, to: statement, connection: connection)
            try bind(barcode.rawValue, at: 2, to: statement, connection: connection)
            guard sqlite3_step(statement) == SQLITE_DONE else { throw queryError(connection: connection) }
        }
    }

    private func openIfNeeded() throws -> UserLibrarySQLiteConnection {
        if let connection { return connection }
        var directoryURL = databaseURL.deletingLastPathComponent()
        do {
            try FileManager.default.createDirectory(at: directoryURL, withIntermediateDirectories: true)
            var values = URLResourceValues(); values.isExcludedFromBackup = true
            try directoryURL.setResourceValues(values)
        } catch { throw UserProductLibraryError.unavailable("local storage directory could not be prepared") }

        var database: OpaquePointer?
        let result = sqlite3_open_v2(databaseURL.path, &database, SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX, nil)
        guard result == SQLITE_OK, let database else {
            let message = String(cString: sqlite3_errstr(result)); if let database { sqlite3_close(database) }
            throw UserProductLibraryError.unavailable(message)
        }
        let opened = UserLibrarySQLiteConnection(handle: database)
        try execute("PRAGMA busy_timeout = 1000;", connection: opened)
        let schemaVersion = try readPragma("user_version", connection: opened)
        let applicationID = try readPragma("application_id", connection: opened)
        if schemaVersion == 0, applicationID == 0 {
            try createSchema(connection: opened)
        } else {
            guard applicationID == Self.expectedApplicationID else {
                throw UserProductLibraryError.invalidRecord("unexpected SQLite application identifier \(applicationID)")
            }
            if schemaVersion == Self.legacyGermanyOnlySchemaVersion {
                try migrateV1ToV2(connection: opened)
            } else if schemaVersion != Self.supportedSchemaVersion {
                throw UserProductLibraryError.incompatibleStore(expected: Self.supportedSchemaVersion, actual: Int(schemaVersion))
            }
        }
        try execute("PRAGMA foreign_keys = ON;", connection: opened)
        connection = opened
        return opened
    }

    private func createSchema(connection: UserLibrarySQLiteConnection) throws {
        try execute("BEGIN IMMEDIATE;", connection: connection)
        do {
            try execute("PRAGMA application_id = \(Self.expectedApplicationID);", connection: connection)
            try execute("PRAGMA user_version = \(Self.supportedSchemaVersion);", connection: connection)
            try execute("CREATE TABLE user_settings(key TEXT PRIMARY KEY, integer_value INTEGER NOT NULL CHECK(integer_value IN (0, 1)));", connection: connection)
            try execute("INSERT INTO user_settings(key, integer_value) VALUES ('history_enabled', 0);", connection: connection)
            try execute("CREATE TABLE scan_history(id INTEGER PRIMARY KEY AUTOINCREMENT, market TEXT NOT NULL CHECK(length(market)=2), gtin TEXT NOT NULL, scanned_at TEXT NOT NULL, catalog_version TEXT NOT NULL, version_marker_json TEXT NOT NULL);", connection: connection)
            try execute("CREATE INDEX idx_scan_history_scanned_at ON scan_history(scanned_at DESC, id DESC);", connection: connection)
            try execute("CREATE TABLE favorites(market TEXT NOT NULL CHECK(length(market)=2), gtin TEXT NOT NULL, saved_at TEXT NOT NULL, catalog_version TEXT NOT NULL, version_marker_json TEXT NOT NULL, PRIMARY KEY(market, gtin));", connection: connection)
            try execute("CREATE INDEX idx_favorites_saved_at ON favorites(saved_at DESC, market ASC, gtin ASC);", connection: connection)
            try execute("COMMIT;", connection: connection)
        } catch { try? execute("ROLLBACK;", connection: connection); throw error }
    }

    private func migrateV1ToV2(connection: UserLibrarySQLiteConnection) throws {
        try execute("BEGIN IMMEDIATE;", connection: connection)
        do {
            // Schema v1 shipped only with the Germany catalog, so DE is the only
            // truthful deterministic migration for legacy references.
            try execute("ALTER TABLE scan_history ADD COLUMN market TEXT NOT NULL DEFAULT 'DE';", connection: connection)
            try execute("ALTER TABLE favorites RENAME TO favorites_v1;", connection: connection)
            try execute("CREATE TABLE favorites(market TEXT NOT NULL CHECK(length(market)=2), gtin TEXT NOT NULL, saved_at TEXT NOT NULL, catalog_version TEXT NOT NULL, version_marker_json TEXT NOT NULL, PRIMARY KEY(market, gtin));", connection: connection)
            try execute("INSERT INTO favorites(market, gtin, saved_at, catalog_version, version_marker_json) SELECT 'DE', gtin, saved_at, catalog_version, version_marker_json FROM favorites_v1;", connection: connection)
            try execute("DROP TABLE favorites_v1;", connection: connection)
            try execute("DROP INDEX IF EXISTS idx_favorites_saved_at;", connection: connection)
            try execute("CREATE INDEX idx_favorites_saved_at ON favorites(saved_at DESC, market ASC, gtin ASC);", connection: connection)
            try execute("PRAGMA user_version = \(Self.supportedSchemaVersion);", connection: connection)
            try execute("COMMIT;", connection: connection)
        } catch { try? execute("ROLLBACK;", connection: connection); throw error }
    }

    private func historyEnabled(connection: UserLibrarySQLiteConnection) throws -> Bool {
        let statement = try prepare("SELECT integer_value FROM user_settings WHERE key = 'history_enabled' LIMIT 1;", connection: connection)
        defer { sqlite3_finalize(statement) }
        guard sqlite3_step(statement) == SQLITE_ROW else { throw UserProductLibraryError.invalidRecord("history opt-in setting is missing") }
        let value = sqlite3_column_int(statement, 0)
        guard value == 0 || value == 1 else { throw UserProductLibraryError.invalidRecord("history opt-in setting is invalid") }
        return value == 1
    }

    private func prepare(_ sql: String, connection: UserLibrarySQLiteConnection) throws -> OpaquePointer {
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(connection.handle, sql, -1, &statement, nil) == SQLITE_OK, let statement else { throw queryError(connection: connection) }
        return statement
    }
    private func bind(_ value: String, at index: Int32, to statement: OpaquePointer, connection: UserLibrarySQLiteConnection) throws {
        let result = value.withCString { sqlite3_bind_text(statement, index, $0, -1, unsafeBitCast(-1, to: sqlite3_destructor_type.self)) }
        guard result == SQLITE_OK else { throw queryError(connection: connection) }
    }
    private func requiredText(_ statement: OpaquePointer, column: Int32) -> String {
        guard let value = sqlite3_column_text(statement, column) else { return "" }; return String(cString: value)
    }
    private func readPragma(_ name: String, connection: UserLibrarySQLiteConnection) throws -> Int32 {
        let statement = try prepare("PRAGMA \(name);", connection: connection); defer { sqlite3_finalize(statement) }
        guard sqlite3_step(statement) == SQLITE_ROW else { throw queryError(connection: connection) }
        return sqlite3_column_int(statement, 0)
    }
    private func execute(_ sql: String, connection: UserLibrarySQLiteConnection) throws {
        guard sqlite3_exec(connection.handle, sql, nil, nil, nil) == SQLITE_OK else { throw queryError(connection: connection) }
    }
    private func queryError(connection: UserLibrarySQLiteConnection) -> UserProductLibraryError {
        .queryFailed(String(cString: sqlite3_errmsg(connection.handle)))
    }

    private static func markerJSON(_ marker: SavedProductVersionMarker) throws -> String {
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]; encoder.dateEncodingStrategy = .iso8601
        let data = try encoder.encode(marker)
        guard let value = String(data: data, encoding: .utf8) else { throw UserProductLibraryError.invalidRecord("version marker is not UTF-8") }
        return value
    }
    private static func decodeMarker(_ value: String) throws -> SavedProductVersionMarker {
        guard let data = value.data(using: .utf8) else { throw UserProductLibraryError.invalidRecord("version marker is not UTF-8") }
        let decoder = JSONDecoder(); decoder.dateDecodingStrategy = .iso8601
        do { return try decoder.decode(SavedProductVersionMarker.self, from: data) }
        catch { throw UserProductLibraryError.invalidRecord("saved product version marker is invalid") }
    }
    private static func dateString(_ date: Date) -> String {
        ISO8601DateFormatter().string(from: date)
    }
    private static func parseDate(_ value: String) throws -> Date {
        guard let date = ISO8601DateFormatter().date(from: value) else { throw UserProductLibraryError.invalidRecord("stored date is invalid") }
        return date
    }
}
