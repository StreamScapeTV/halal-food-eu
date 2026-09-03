import Foundation
import SQLite3

private final class UserLibrarySQLiteConnection: @unchecked Sendable {
    let handle: OpaquePointer

    init(handle: OpaquePointer) {
        self.handle = handle
    }

    deinit {
        sqlite3_close(handle)
    }
}

actor SQLiteUserProductLibrary: UserProductLibraryStore {
    static let supportedSchemaVersion = 1
    static let expectedApplicationID: Int32 = 1_212_568_900 // ASCII "HFUD"
    static let maximumHistoryEntries = 200

    private let databaseURL: URL
    private var connection: UserLibrarySQLiteConnection?

    init(databaseURL: URL) {
        self.databaseURL = databaseURL
    }

    func isHistoryEnabled() async throws -> Bool {
        try Task.checkCancellation()
        let connection = try openIfNeeded()
        let statement = try prepare(
            "SELECT integer_value FROM user_settings WHERE key = 'history_enabled' LIMIT 1;",
            connection: connection
        )
        defer { sqlite3_finalize(statement) }

        guard sqlite3_step(statement) == SQLITE_ROW else {
            throw UserProductLibraryError.invalidRecord("history opt-in setting is missing")
        }
        return sqlite3_column_int(statement, 0) == 1
    }

    func setHistoryEnabled(_ enabled: Bool) async throws {
        try Task.checkCancellation()
        let connection = try openIfNeeded()
        let statement = try prepare(
            "UPDATE user_settings SET integer_value = ? WHERE key = 'history_enabled';",
            connection: connection
        )
        defer { sqlite3_finalize(statement) }
        guard sqlite3_bind_int(statement, 1, enabled ? 1 : 0) == SQLITE_OK,
              sqlite3_step(statement) == SQLITE_DONE else {
            throw queryError(connection: connection)
        }
    }

    func recordScan(
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
        guard try historyEnabled(connection: connection) else { return }

        let statement = try prepare(
            """
            INSERT INTO scan_history(gtin, scanned_at, catalog_version, version_marker_json)
            VALUES (?, ?, ?, ?);
            """,
            connection: connection
        )
        defer { sqlite3_finalize(statement) }
        try bind(barcode.rawValue, at: 1, to: statement, connection: connection)
        try bind(Self.dateString(scannedAt), at: 2, to: statement, connection: connection)
        try bind(catalogVersion, at: 3, to: statement, connection: connection)
        try bind(try Self.markerJSON(versionMarker), at: 4, to: statement, connection: connection)
        guard sqlite3_step(statement) == SQLITE_DONE else {
            throw queryError(connection: connection)
        }

        let trim = try prepare(
            """
            DELETE FROM scan_history
            WHERE id IN (
                SELECT id
                FROM scan_history
                ORDER BY scanned_at DESC, id DESC
                LIMIT -1 OFFSET ?
            );
            """,
            connection: connection
        )
        defer { sqlite3_finalize(trim) }
        guard sqlite3_bind_int(trim, 1, Int32(Self.maximumHistoryEntries)) == SQLITE_OK,
              sqlite3_step(trim) == SQLITE_DONE else {
            throw queryError(connection: connection)
        }
    }

    func history(limit: Int = SQLiteUserProductLibrary.maximumHistoryEntries) async throws -> [ScanHistoryEntry] {
        guard (1...Self.maximumHistoryEntries).contains(limit) else {
            throw UserProductLibraryError.invalidRecord("history page limit is outside supported bounds")
        }
        try Task.checkCancellation()
        let connection = try openIfNeeded()
        let statement = try prepare(
            """
            SELECT id, gtin, scanned_at, catalog_version, version_marker_json
            FROM scan_history
            ORDER BY scanned_at DESC, id DESC
            LIMIT ?;
            """,
            connection: connection
        )
        defer { sqlite3_finalize(statement) }
        guard sqlite3_bind_int(statement, 1, Int32(limit)) == SQLITE_OK else {
            throw queryError(connection: connection)
        }

        var entries: [ScanHistoryEntry] = []
        while true {
            try Task.checkCancellation()
            switch sqlite3_step(statement) {
            case SQLITE_ROW:
                let barcode = try Barcode(validating: requiredText(statement, column: 1))
                let scannedAt = try Self.parseDate(requiredText(statement, column: 2))
                let catalogVersion = requiredText(statement, column: 3)
                guard !catalogVersion.isEmpty else {
                    throw UserProductLibraryError.invalidRecord("history entry has an empty catalog version")
                }
                let marker = try Self.decodeMarker(requiredText(statement, column: 4))
                entries.append(
                    ScanHistoryEntry(
                        id: sqlite3_column_int64(statement, 0),
                        barcode: barcode,
                        scannedAt: scannedAt,
                        catalogVersion: catalogVersion,
                        versionMarker: marker
                    )
                )
            case SQLITE_DONE:
                return entries
            default:
                throw queryError(connection: connection)
            }
        }
    }

    func deleteHistoryEntry(id: Int64) async throws {
        try Task.checkCancellation()
        let connection = try openIfNeeded()
        let statement = try prepare("DELETE FROM scan_history WHERE id = ?;", connection: connection)
        defer { sqlite3_finalize(statement) }
        guard sqlite3_bind_int64(statement, 1, id) == SQLITE_OK,
              sqlite3_step(statement) == SQLITE_DONE else {
            throw queryError(connection: connection)
        }
    }

    func clearHistory() async throws {
        try Task.checkCancellation()
        let connection = try openIfNeeded()
        try execute("DELETE FROM scan_history;", connection: connection)
    }

    func favorites() async throws -> [FavoriteProduct] {
        try Task.checkCancellation()
        let connection = try openIfNeeded()
        let statement = try prepare(
            """
            SELECT gtin, saved_at, catalog_version, version_marker_json
            FROM favorites
            ORDER BY saved_at DESC, gtin ASC;
            """,
            connection: connection
        )
        defer { sqlite3_finalize(statement) }

        var favorites: [FavoriteProduct] = []
        while true {
            try Task.checkCancellation()
            switch sqlite3_step(statement) {
            case SQLITE_ROW:
                let barcode = try Barcode(validating: requiredText(statement, column: 0))
                let savedAt = try Self.parseDate(requiredText(statement, column: 1))
                let catalogVersion = requiredText(statement, column: 2)
                guard !catalogVersion.isEmpty else {
                    throw UserProductLibraryError.invalidRecord("favorite has an empty catalog version")
                }
                favorites.append(
                    FavoriteProduct(
                        barcode: barcode,
                        savedAt: savedAt,
                        catalogVersion: catalogVersion,
                        versionMarker: try Self.decodeMarker(requiredText(statement, column: 3))
                    )
                )
            case SQLITE_DONE:
                return favorites
            default:
                throw queryError(connection: connection)
            }
        }
    }

    func favorite(for barcode: Barcode) async throws -> FavoriteProduct? {
        try Task.checkCancellation()
        let connection = try openIfNeeded()
        let statement = try prepare(
            """
            SELECT saved_at, catalog_version, version_marker_json
            FROM favorites
            WHERE gtin = ?
            LIMIT 1;
            """,
            connection: connection
        )
        defer { sqlite3_finalize(statement) }
        try bind(barcode.rawValue, at: 1, to: statement, connection: connection)

        switch sqlite3_step(statement) {
        case SQLITE_ROW:
            let catalogVersion = requiredText(statement, column: 1)
            guard !catalogVersion.isEmpty else {
                throw UserProductLibraryError.invalidRecord("favorite has an empty catalog version")
            }
            return FavoriteProduct(
                barcode: barcode,
                savedAt: try Self.parseDate(requiredText(statement, column: 0)),
                catalogVersion: catalogVersion,
                versionMarker: try Self.decodeMarker(requiredText(statement, column: 2))
            )
        case SQLITE_DONE:
            return nil
        default:
            throw queryError(connection: connection)
        }
    }

    func setFavorite(
        barcode: Barcode,
        savedAt: Date,
        catalogVersion: String,
        versionMarker: SavedProductVersionMarker,
        isFavorite: Bool
    ) async throws {
        guard !catalogVersion.isEmpty else {
            throw UserProductLibraryError.invalidRecord("favorite catalog version is empty")
        }
        try Task.checkCancellation()
        let connection = try openIfNeeded()

        if isFavorite {
            let statement = try prepare(
                """
                INSERT INTO favorites(gtin, saved_at, catalog_version, version_marker_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(gtin) DO UPDATE SET
                    saved_at = excluded.saved_at,
                    catalog_version = excluded.catalog_version,
                    version_marker_json = excluded.version_marker_json;
                """,
                connection: connection
            )
            defer { sqlite3_finalize(statement) }
            try bind(barcode.rawValue, at: 1, to: statement, connection: connection)
            try bind(Self.dateString(savedAt), at: 2, to: statement, connection: connection)
            try bind(catalogVersion, at: 3, to: statement, connection: connection)
            try bind(try Self.markerJSON(versionMarker), at: 4, to: statement, connection: connection)
            guard sqlite3_step(statement) == SQLITE_DONE else {
                throw queryError(connection: connection)
            }
        } else {
            let statement = try prepare("DELETE FROM favorites WHERE gtin = ?;", connection: connection)
            defer { sqlite3_finalize(statement) }
            try bind(barcode.rawValue, at: 1, to: statement, connection: connection)
            guard sqlite3_step(statement) == SQLITE_DONE else {
                throw queryError(connection: connection)
            }
        }
    }

    private func openIfNeeded() throws -> UserLibrarySQLiteConnection {
        if let connection { return connection }

        var directoryURL = databaseURL.deletingLastPathComponent()
        do {
            try FileManager.default.createDirectory(
                at: directoryURL,
                withIntermediateDirectories: true
            )
            var resourceValues = URLResourceValues()
            resourceValues.isExcludedFromBackup = true
            try directoryURL.setResourceValues(resourceValues)
        } catch {
            throw UserProductLibraryError.unavailable("local storage directory could not be prepared")
        }

        var openedDatabase: OpaquePointer?
        let flags = SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX
        let openResult = sqlite3_open_v2(databaseURL.path, &openedDatabase, flags, nil)
        guard openResult == SQLITE_OK, let openedDatabase else {
            let message = String(cString: sqlite3_errstr(openResult))
            if let openedDatabase { sqlite3_close(openedDatabase) }
            throw UserProductLibraryError.unavailable(message)
        }

        let openedConnection = UserLibrarySQLiteConnection(handle: openedDatabase)
        try execute("PRAGMA busy_timeout = 1000;", connection: openedConnection)
        let schemaVersion = try readPragma("user_version", connection: openedConnection)
        let applicationID = try readPragma("application_id", connection: openedConnection)

        if schemaVersion == 0, applicationID == 0 {
            try createSchema(connection: openedConnection)
        } else {
            guard applicationID == Self.expectedApplicationID else {
                throw UserProductLibraryError.invalidRecord(
                    "unexpected SQLite application identifier \(applicationID)"
                )
            }
            guard schemaVersion == Self.supportedSchemaVersion else {
                throw UserProductLibraryError.incompatibleStore(
                    expected: Self.supportedSchemaVersion,
                    actual: Int(schemaVersion)
                )
            }
        }

        try execute("PRAGMA foreign_keys = ON;", connection: openedConnection)
        connection = openedConnection
        return openedConnection
    }

    private func createSchema(connection: UserLibrarySQLiteConnection) throws {
        try execute("BEGIN IMMEDIATE;", connection: connection)
        do {
            try execute("PRAGMA application_id = \(Self.expectedApplicationID);", connection: connection)
            try execute("PRAGMA user_version = \(Self.supportedSchemaVersion);", connection: connection)
            try execute(
                """
                CREATE TABLE user_settings(
                    key TEXT PRIMARY KEY,
                    integer_value INTEGER NOT NULL CHECK(integer_value IN (0, 1))
                );
                """,
                connection: connection
            )
            try execute(
                "INSERT INTO user_settings(key, integer_value) VALUES ('history_enabled', 0);",
                connection: connection
            )
            try execute(
                """
                CREATE TABLE scan_history(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    gtin TEXT NOT NULL,
                    scanned_at TEXT NOT NULL,
                    catalog_version TEXT NOT NULL,
                    version_marker_json TEXT NOT NULL
                );
                """,
                connection: connection
            )
            try execute(
                "CREATE INDEX idx_scan_history_scanned_at ON scan_history(scanned_at DESC, id DESC);",
                connection: connection
            )
            try execute(
                """
                CREATE TABLE favorites(
                    gtin TEXT PRIMARY KEY,
                    saved_at TEXT NOT NULL,
                    catalog_version TEXT NOT NULL,
                    version_marker_json TEXT NOT NULL
                );
                """,
                connection: connection
            )
            try execute(
                "CREATE INDEX idx_favorites_saved_at ON favorites(saved_at DESC, gtin ASC);",
                connection: connection
            )
            try execute("COMMIT;", connection: connection)
        } catch {
            try? execute("ROLLBACK;", connection: connection)
            throw error
        }
    }

    private func historyEnabled(connection: UserLibrarySQLiteConnection) throws -> Bool {
        let statement = try prepare(
            "SELECT integer_value FROM user_settings WHERE key = 'history_enabled' LIMIT 1;",
            connection: connection
        )
        defer { sqlite3_finalize(statement) }
        guard sqlite3_step(statement) == SQLITE_ROW else {
            throw UserProductLibraryError.invalidRecord("history opt-in setting is missing")
        }
        return sqlite3_column_int(statement, 0) == 1
    }

    private func prepare(
        _ sql: String,
        connection: UserLibrarySQLiteConnection
    ) throws -> OpaquePointer {
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(connection.handle, sql, -1, &statement, nil) == SQLITE_OK,
              let statement else {
            throw queryError(connection: connection)
        }
        return statement
    }

    private func bind(
        _ value: String,
        at index: Int32,
        to statement: OpaquePointer,
        connection: UserLibrarySQLiteConnection
    ) throws {
        let result = value.withCString { pointer in
            sqlite3_bind_text(
                statement,
                index,
                pointer,
                -1,
                unsafeBitCast(-1, to: sqlite3_destructor_type.self)
            )
        }
        guard result == SQLITE_OK else { throw queryError(connection: connection) }
    }

    private func requiredText(_ statement: OpaquePointer, column: Int32) -> String {
        guard let value = sqlite3_column_text(statement, column) else { return "" }
        return String(cString: value)
    }

    private func readPragma(
        _ name: String,
        connection: UserLibrarySQLiteConnection
    ) throws -> Int32 {
        let statement = try prepare("PRAGMA \(name);", connection: connection)
        defer { sqlite3_finalize(statement) }
        guard sqlite3_step(statement) == SQLITE_ROW else {
            throw queryError(connection: connection)
        }
        return sqlite3_column_int(statement, 0)
    }

    private func execute(
        _ sql: String,
        connection: UserLibrarySQLiteConnection
    ) throws {
        guard sqlite3_exec(connection.handle, sql, nil, nil, nil) == SQLITE_OK else {
            throw queryError(connection: connection)
        }
    }

    private func queryError(connection: UserLibrarySQLiteConnection) -> UserProductLibraryError {
        .queryFailed(String(cString: sqlite3_errmsg(connection.handle)))
    }

    private static func markerJSON(_ marker: SavedProductVersionMarker) throws -> String {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        encoder.dateEncodingStrategy = .iso8601
        let data = try encoder.encode(marker)
        guard let value = String(data: data, encoding: .utf8) else {
            throw UserProductLibraryError.invalidRecord("version marker is not UTF-8")
        }
        return value
    }

    private static func decodeMarker(_ value: String) throws -> SavedProductVersionMarker {
        guard let data = value.data(using: .utf8) else {
            throw UserProductLibraryError.invalidRecord("version marker is not UTF-8")
        }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        do {
            return try decoder.decode(SavedProductVersionMarker.self, from: data)
        } catch {
            throw UserProductLibraryError.invalidRecord("version marker is malformed")
        }
    }

    private static func dateString(_ date: Date) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter.string(from: date)
    }

    private static func parseDate(_ value: String) throws -> Date {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        guard let date = formatter.date(from: value) else {
            throw UserProductLibraryError.invalidRecord("stored date is not ISO-8601")
        }
        return date
    }
}
