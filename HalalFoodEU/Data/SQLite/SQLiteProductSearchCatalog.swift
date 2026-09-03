import CryptoKit
import Foundation
import SQLite3

private final class ProductSearchSQLiteConnection: @unchecked Sendable {
    let handle: OpaquePointer

    init(handle: OpaquePointer) {
        self.handle = handle
    }

    deinit {
        sqlite3_close(handle)
    }
}

private struct ProductSearchCatalogManifest: Decodable, Sendable {
    struct SearchIndex: Decodable, Equatable, Sendable {
        let schemaVersion: Int
        let engine: String
        let ftsTable: String
        let barcodeAliasTable: String
        let tokenizer: String
        let prefixIndexes: [Int]
        let maxPageSize: Int
    }

    let manifestSchemaVersion: Int
    let schemaVersion: Int
    let databaseBytes: Int
    let sha256: String
    let searchIndex: SearchIndex?
}

actor SQLiteProductSearchCatalog: ProductSearchCatalog {
    static let supportedManifestSchemaVersion = 3
    static let supportedSchemaVersion = 2
    static let supportedSearchIndexSchemaVersion = 1
    static let expectedApplicationID: Int32 = 1_212_564_821 // ASCII "HFEU"
    static let expectedSearchIndex = ProductSearchCatalogManifest.SearchIndex(
        schemaVersion: supportedSearchIndexSchemaVersion,
        engine: "sqlite-fts5",
        ftsTable: "product_search",
        barcodeAliasTable: "product_barcode_aliases",
        tokenizer: "unicode61 remove_diacritics 2",
        prefixIndexes: [2, 3, 4],
        maxPageSize: SearchProducts.maximumPageSize
    )

    private let databaseURL: URL
    private let manifestURL: URL
    private var connection: ProductSearchSQLiteConnection?

    init(databaseURL: URL, manifestURL: URL) {
        self.databaseURL = databaseURL
        self.manifestURL = manifestURL
    }

    func search(query: String, limit: Int, offset: Int) async throws -> ProductSearchPage {
        try Task.checkCancellation()
        guard (1...SearchProducts.maximumPageSize).contains(limit) else {
            throw ProductSearchError.invalidPageSize(maximum: SearchProducts.maximumPageSize)
        }
        guard offset >= 0 else {
            throw ProductSearchError.invalidOffset
        }

        let connection = try openIfNeeded()
        if let numeric = Self.numericQuery(query) {
            return try barcodeResults(
                prefix: numeric,
                limit: limit,
                offset: offset,
                connection: connection
            )
        }
        guard let expression = Self.ftsExpression(query) else {
            return .empty
        }
        return try textResults(
            expression: expression,
            limit: limit,
            offset: offset,
            connection: connection
        )
    }

    private func barcodeResults(
        prefix: String,
        limit: Int,
        offset: Int,
        connection: ProductSearchSQLiteConnection
    ) throws -> ProductSearchPage {
        let sql = """
            WITH matches AS (
                SELECT
                    gtin,
                    MIN(CASE WHEN alias = ?3 THEN 0 ELSE 1 END) AS exact_rank,
                    MIN(length(alias)) AS alias_length,
                    MIN(alias) AS best_alias
                FROM product_barcode_aliases
                WHERE alias >= ?1 AND alias < ?2
                GROUP BY gtin
            )
            SELECT
                p.gtin,
                p.name,
                p.brand,
                p.quantity,
                matches.exact_rank
            FROM matches
            JOIN products AS p ON p.gtin = matches.gtin
            ORDER BY
                matches.exact_rank ASC,
                matches.alias_length ASC,
                matches.best_alias ASC,
                p.name COLLATE NOCASE ASC,
                p.gtin ASC
            LIMIT ?4 OFFSET ?5;
            """
        let statement = try prepare(sql, connection: connection)
        defer { sqlite3_finalize(statement) }
        try bind(prefix, at: 1, to: statement, connection: connection)
        try bind(prefix + ":", at: 2, to: statement, connection: connection)
        try bind(prefix, at: 3, to: statement, connection: connection)
        try bind(limit + 1, at: 4, to: statement, connection: connection)
        try bind(offset, at: 5, to: statement, connection: connection)

        var results: [ProductSearchResult] = []
        while results.count <= limit {
            try Task.checkCancellation()
            switch sqlite3_step(statement) {
            case SQLITE_ROW:
                let barcode = try Barcode(validating: requiredText(statement, column: 0))
                results.append(
                    ProductSearchResult(
                        barcode: barcode,
                        name: requiredText(statement, column: 1),
                        brand: optionalText(statement, column: 2),
                        quantity: optionalText(statement, column: 3),
                        matchKind: sqlite3_column_int(statement, 4) == 0 ? .barcodeExact : .barcodePrefix
                    )
                )
            case SQLITE_DONE:
                let hasMore = results.count > limit
                return ProductSearchPage(
                    results: Array(results.prefix(limit)),
                    offset: offset,
                    hasMore: hasMore
                )
            default:
                throw queryError(connection: connection)
            }
        }
        return ProductSearchPage(
            results: Array(results.prefix(limit)),
            offset: offset,
            hasMore: true
        )
    }

    private func textResults(
        expression: String,
        limit: Int,
        offset: Int,
        connection: ProductSearchSQLiteConnection
    ) throws -> ProductSearchPage {
        let sql = """
            SELECT
                search.gtin,
                p.name,
                p.brand,
                p.quantity
            FROM product_search AS search
            JOIN products AS p ON p.gtin = search.gtin
            WHERE product_search MATCH ?1
            ORDER BY
                bm25(product_search, 0.0, 4.0, 2.0) ASC,
                p.name COLLATE NOCASE ASC,
                p.gtin ASC
            LIMIT ?2 OFFSET ?3;
            """
        let statement = try prepare(sql, connection: connection)
        defer { sqlite3_finalize(statement) }
        try bind(expression, at: 1, to: statement, connection: connection)
        try bind(limit + 1, at: 2, to: statement, connection: connection)
        try bind(offset, at: 3, to: statement, connection: connection)

        var results: [ProductSearchResult] = []
        while results.count <= limit {
            try Task.checkCancellation()
            switch sqlite3_step(statement) {
            case SQLITE_ROW:
                let barcode = try Barcode(validating: requiredText(statement, column: 0))
                results.append(
                    ProductSearchResult(
                        barcode: barcode,
                        name: requiredText(statement, column: 1),
                        brand: optionalText(statement, column: 2),
                        quantity: optionalText(statement, column: 3),
                        matchKind: .text
                    )
                )
            case SQLITE_DONE:
                let hasMore = results.count > limit
                return ProductSearchPage(
                    results: Array(results.prefix(limit)),
                    offset: offset,
                    hasMore: hasMore
                )
            default:
                throw queryError(connection: connection)
            }
        }
        return ProductSearchPage(
            results: Array(results.prefix(limit)),
            offset: offset,
            hasMore: true
        )
    }

    private func openIfNeeded() throws -> ProductSearchSQLiteConnection {
        if let connection { return connection }

        let manifest: ProductSearchCatalogManifest
        do {
            let data = try Data(contentsOf: manifestURL, options: [.mappedIfSafe])
            manifest = try JSONDecoder().decode(ProductSearchCatalogManifest.self, from: data)
        } catch {
            throw ProductCatalogError.unavailable("catalog-manifest.json cannot be decoded for search")
        }
        guard manifest.manifestSchemaVersion == Self.supportedManifestSchemaVersion else {
            throw ProductCatalogError.invalidRecord(
                "catalog manifest schema \(manifest.manifestSchemaVersion) is unsupported for search"
            )
        }
        guard manifest.schemaVersion == Self.supportedSchemaVersion else {
            throw ProductCatalogError.incompatibleSchema(
                expected: Self.supportedSchemaVersion,
                actual: manifest.schemaVersion
            )
        }
        guard manifest.searchIndex == Self.expectedSearchIndex else {
            throw ProductCatalogError.invalidRecord("bundled catalog search-index metadata is missing or unsupported")
        }
        let resourceValues: URLResourceValues
        do {
            resourceValues = try databaseURL.resourceValues(forKeys: [.fileSizeKey])
        } catch {
            throw ProductCatalogError.unavailable("catalog.sqlite3 size cannot be read for search")
        }
        guard resourceValues.fileSize == manifest.databaseBytes else {
            throw ProductCatalogError.invalidRecord("bundled catalog byte size does not match its manifest")
        }
        let actualDigest = try Self.fileSHA256(databaseURL)
        guard manifest.sha256 == actualDigest else {
            throw ProductCatalogError.invalidRecord("bundled catalog SHA-256 does not match its manifest")
        }

        var openedDatabase: OpaquePointer?
        let flags = SQLITE_OPEN_READONLY | SQLITE_OPEN_FULLMUTEX
        let openResult = sqlite3_open_v2(databaseURL.path, &openedDatabase, flags, nil)
        guard openResult == SQLITE_OK, let openedDatabase else {
            let message = String(cString: sqlite3_errstr(openResult))
            if let openedDatabase { sqlite3_close(openedDatabase) }
            throw ProductCatalogError.unavailable(message)
        }

        let openedConnection = ProductSearchSQLiteConnection(handle: openedDatabase)
        guard sqlite3_exec(openedDatabase, "PRAGMA query_only = ON;", nil, nil, nil) == SQLITE_OK else {
            throw queryError(connection: openedConnection)
        }
        let applicationID = try readIntegerPragma("application_id", connection: openedConnection)
        guard applicationID == Self.expectedApplicationID else {
            throw ProductCatalogError.invalidRecord("unexpected SQLite application identifier \(applicationID)")
        }
        let schemaVersion = try readIntegerPragma("user_version", connection: openedConnection)
        guard schemaVersion == Self.supportedSchemaVersion else {
            throw ProductCatalogError.incompatibleSchema(
                expected: Self.supportedSchemaVersion,
                actual: Int(schemaVersion)
            )
        }
        let searchObjects = try readCount(
            """
            SELECT COUNT(*)
            FROM sqlite_master
            WHERE name IN ('product_search', 'product_barcode_aliases');
            """,
            connection: openedConnection
        )
        guard searchObjects == 2 else {
            throw ProductCatalogError.invalidRecord("bundled catalog search-index tables are incomplete")
        }
        let productCount = try readCount("SELECT COUNT(*) FROM products;", connection: openedConnection)
        let searchCount = try readCount("SELECT COUNT(*) FROM product_search;", connection: openedConnection)
        guard productCount == searchCount else {
            throw ProductCatalogError.invalidRecord("bundled catalog search-index row count differs from products")
        }
        let canonicalAliasCount = try readCount(
            "SELECT COUNT(*) FROM product_barcode_aliases WHERE length(alias)=14;",
            connection: openedConnection
        )
        guard productCount == canonicalAliasCount else {
            throw ProductCatalogError.invalidRecord("bundled catalog barcode-alias coverage differs from products")
        }

        connection = openedConnection
        return openedConnection
    }

    private static func numericQuery(_ query: String) -> String? {
        let stripped = query.filter { character in
            !character.isWhitespace && character != "-"
        }
        guard !stripped.isEmpty, stripped.count <= 14 else { return nil }
        guard stripped.unicodeScalars.allSatisfy({ scalar in
            scalar.value >= 48 && scalar.value <= 57
        }) else {
            return nil
        }
        return stripped
    }

    private static func ftsExpression(_ query: String) -> String? {
        let tokens = query
            .components(separatedBy: CharacterSet.alphanumerics.inverted)
            .filter { !$0.isEmpty }
            .prefix(8)
        guard !tokens.isEmpty else { return nil }
        return tokens.map { token in
            let bounded = String(token.prefix(64))
            let escaped = bounded.replacingOccurrences(of: "\"", with: "\"\"")
            return "\"\(escaped)\"*"
        }
        .joined(separator: " AND ")
    }

    private static func fileSHA256(_ url: URL) throws -> String {
        let handle: FileHandle
        do {
            handle = try FileHandle(forReadingFrom: url)
        } catch {
            throw ProductCatalogError.unavailable("catalog.sqlite3 cannot be opened for integrity validation")
        }
        defer { try? handle.close() }

        var hasher = SHA256()
        do {
            while let data = try handle.read(upToCount: 1024 * 1024), !data.isEmpty {
                try Task.checkCancellation()
                hasher.update(data: data)
            }
        } catch is CancellationError {
            throw CancellationError()
        } catch {
            throw ProductCatalogError.unavailable("catalog.sqlite3 cannot be read for integrity validation")
        }
        return hasher.finalize().map { String(format: "%02x", $0) }.joined()
    }

    private func prepare(
        _ sql: String,
        connection: ProductSearchSQLiteConnection
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
        connection: ProductSearchSQLiteConnection
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

    private func bind(
        _ value: Int,
        at index: Int32,
        to statement: OpaquePointer,
        connection: ProductSearchSQLiteConnection
    ) throws {
        guard sqlite3_bind_int64(statement, index, Int64(value)) == SQLITE_OK else {
            throw queryError(connection: connection)
        }
    }

    private func requiredText(_ statement: OpaquePointer, column: Int32) -> String {
        guard let text = sqlite3_column_text(statement, column) else { return "" }
        return String(cString: text)
    }

    private func optionalText(_ statement: OpaquePointer, column: Int32) -> String? {
        guard sqlite3_column_type(statement, column) != SQLITE_NULL,
              let text = sqlite3_column_text(statement, column) else {
            return nil
        }
        return String(cString: text)
    }

    private func readIntegerPragma(
        _ name: String,
        connection: ProductSearchSQLiteConnection
    ) throws -> Int32 {
        let statement = try pragmaStatement(name, connection: connection)
        defer { sqlite3_finalize(statement) }
        guard sqlite3_step(statement) == SQLITE_ROW else {
            throw queryError(connection: connection)
        }
        return sqlite3_column_int(statement, 0)
    }

    private func pragmaStatement(
        _ name: String,
        connection: ProductSearchSQLiteConnection
    ) throws -> OpaquePointer {
        try prepare("PRAGMA \(name);", connection: connection)
    }

    private func readCount(
        _ sql: String,
        connection: ProductSearchSQLiteConnection
    ) throws -> Int {
        let statement = try prepare(sql, connection: connection)
        defer { sqlite3_finalize(statement) }
        guard sqlite3_step(statement) == SQLITE_ROW else {
            throw queryError(connection: connection)
        }
        return Int(sqlite3_column_int64(statement, 0))
    }

    private func queryError(connection: ProductSearchSQLiteConnection) -> ProductCatalogError {
        ProductCatalogError.queryFailed(String(cString: sqlite3_errmsg(connection.handle)))
    }
}
