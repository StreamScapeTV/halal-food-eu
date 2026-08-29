import CryptoKit
import Foundation
import SQLite3

private final class SQLiteConnection: @unchecked Sendable {
    let handle: OpaquePointer

    init(handle: OpaquePointer) {
        self.handle = handle
    }

    deinit {
        sqlite3_close(handle)
    }
}

private struct CatalogManifest: Decodable, Sendable {
    struct SourcePolicy: Decodable, Sendable {
        let schemaVersion: Int
        let contractVersion: String
        let sha256: String
    }

    let catalogVersion: String
    let schemaVersion: Int
    let recordCount: Int
    let sha256: String
    let sourcePolicy: SourcePolicy
}

actor SQLiteProductCatalog: ProductCatalog {
    static let supportedSchemaVersion = 1
    static let supportedSourcePolicySchemaVersion = 1
    static let expectedApplicationID: Int32 = 1_212_564_821 // ASCII "HFEU"

    private static let requiredTables: Set<String> = [
        "catalog_metadata",
        "sources",
        "products",
        "product_observations",
        "product_assessments",
        "certification_evidence",
        "assessment_reasons",
    ]

    private let databaseURL: URL
    private let manifestURL: URL
    private var connection: SQLiteConnection?
    private var catalogVersion: String?

    init(databaseURL: URL, manifestURL: URL) {
        self.databaseURL = databaseURL
        self.manifestURL = manifestURL
    }

    func product(for barcode: Barcode) async throws -> ProductRecord? {
        try Task.checkCancellation()
        let (connection, catalogVersion) = try openIfNeeded()

        let productSQL = """
            SELECT
                p.gtin,
                p.name,
                p.brand,
                o.ingredients_text,
                o.language_code,
                o.observed_at,
                o.ingredients_hash,
                s.name,
                s.kind,
                s.reference,
                s.license,
                s.retrieved_at,
                a.id,
                a.status,
                a.summary,
                a.methodology_version,
                a.reviewed_at
            FROM products AS p
            JOIN product_observations AS o ON o.id = p.current_observation_id
            JOIN sources AS s ON s.id = o.source_id
            JOIN product_assessments AS a ON a.observation_id = o.id
            WHERE p.gtin = ?1
            LIMIT 1;
            """

        let statement = try prepare(productSQL, connection: connection)
        defer { sqlite3_finalize(statement) }

        try bind(barcode.rawValue, at: 1, to: statement, connection: connection)

        let stepResult = sqlite3_step(statement)
        if stepResult == SQLITE_DONE {
            return nil
        }
        guard stepResult == SQLITE_ROW else {
            throw queryError(connection: connection)
        }

        let storedBarcode = try Barcode(validating: requiredText(statement, column: 0))
        let name = requiredText(statement, column: 1)
        let brand = optionalText(statement, column: 2)
        let ingredientsText = requiredText(statement, column: 3)
        let languageCode = requiredText(statement, column: 4)
        let observedAt = try parseDate(requiredText(statement, column: 5), field: "observed_at")
        let ingredientsHash = requiredText(statement, column: 6)
        let sourceName = requiredText(statement, column: 7)
        let sourceKind = requiredText(statement, column: 8)
        let sourceReference = requiredText(statement, column: 9)
        let sourceLicense = requiredText(statement, column: 10)
        let retrievedAt = try parseDate(requiredText(statement, column: 11), field: "retrieved_at")
        let assessmentID = sqlite3_column_int64(statement, 12)
        let rawStatus = requiredText(statement, column: 13)
        let summary = requiredText(statement, column: 14)
        let methodologyVersion = requiredText(statement, column: 15)
        let reviewedAt = try parseDate(requiredText(statement, column: 16), field: "reviewed_at")

        guard let status = HalalStatus(rawValue: rawStatus) else {
            throw ProductCatalogError.invalidRecord("unsupported halal status \(rawStatus)")
        }

        let reasons = try reasons(for: assessmentID, connection: connection)
        guard !reasons.isEmpty else {
            throw ProductCatalogError.invalidRecord("assessment \(assessmentID) has no reasons")
        }

        let certifications = try certifications(for: assessmentID, connection: connection)
        if status == .halalCertified, certifications.isEmpty {
            throw ProductCatalogError.invalidRecord(
                "certified assessment \(assessmentID) has no certification evidence"
            )
        }

        return ProductRecord(
            barcode: storedBarcode,
            name: name,
            brand: brand,
            observation: IngredientObservation(
                text: ingredientsText,
                languageCode: languageCode,
                observedAt: observedAt,
                contentHash: ingredientsHash,
                source: ProductSource(
                    name: sourceName,
                    kind: sourceKind,
                    reference: sourceReference,
                    license: sourceLicense,
                    retrievedAt: retrievedAt
                )
            ),
            assessment: HalalAssessment(
                status: status,
                summary: summary,
                methodologyVersion: methodologyVersion,
                reviewedAt: reviewedAt,
                reasons: reasons,
                certifications: certifications
            ),
            catalogVersion: catalogVersion
        )
    }

    private func openIfNeeded() throws -> (SQLiteConnection, String) {
        if let connection, let catalogVersion {
            return (connection, catalogVersion)
        }

        let manifest = try Self.loadAndValidateManifest(manifestURL: manifestURL, databaseURL: databaseURL)

        var openedDatabase: OpaquePointer?
        let flags = SQLITE_OPEN_READONLY | SQLITE_OPEN_FULLMUTEX
        let openResult = sqlite3_open_v2(databaseURL.path, &openedDatabase, flags, nil)

        guard openResult == SQLITE_OK, let openedDatabase else {
            let message = String(cString: sqlite3_errstr(openResult))
            if let openedDatabase {
                sqlite3_close(openedDatabase)
            }
            throw ProductCatalogError.unavailable(message)
        }

        let openedConnection = SQLiteConnection(handle: openedDatabase)
        try Self.execute("PRAGMA query_only = ON;", database: openedDatabase)
        try Self.execute("PRAGMA foreign_keys = ON;", database: openedDatabase)

        let queryOnly = try Self.readIntegerPragma("query_only", database: openedDatabase)
        guard queryOnly == 1 else {
            throw ProductCatalogError.invalidRecord("SQLite query-only mode was not enabled")
        }

        let applicationID = try Self.readIntegerPragma("application_id", database: openedDatabase)
        guard applicationID == Self.expectedApplicationID else {
            throw ProductCatalogError.invalidRecord(
                "unexpected SQLite application identifier \(applicationID)"
            )
        }

        let schemaVersion = try Self.readIntegerPragma("user_version", database: openedDatabase)
        guard schemaVersion == Self.supportedSchemaVersion else {
            throw ProductCatalogError.incompatibleSchema(
                expected: Self.supportedSchemaVersion,
                actual: Int(schemaVersion)
            )
        }
        guard manifest.schemaVersion == Int(schemaVersion) else {
            throw ProductCatalogError.invalidRecord("catalog manifest and SQLite schema versions differ")
        }

        try Self.validateIntegrity(database: openedDatabase)
        try Self.validateRequiredTables(database: openedDatabase)

        let openedCatalogVersion = try Self.readMetadata("catalogVersion", database: openedDatabase)
        guard openedCatalogVersion == manifest.catalogVersion else {
            throw ProductCatalogError.invalidRecord("catalog manifest and SQLite catalog versions differ")
        }
        let metadataSchema = try Self.readMetadata("schemaVersion", database: openedDatabase)
        guard metadataSchema == String(manifest.schemaVersion) else {
            throw ProductCatalogError.invalidRecord("catalog metadata schema version differs from manifest")
        }

        let productCount = try Self.readCount(
            "SELECT COUNT(*) FROM products;",
            database: openedDatabase
        )
        guard productCount == manifest.recordCount else {
            throw ProductCatalogError.invalidRecord("catalog manifest record count differs from SQLite")
        }

        connection = openedConnection
        catalogVersion = openedCatalogVersion
        return (openedConnection, openedCatalogVersion)
    }

    private func reasons(
        for assessmentID: Int64,
        connection: SQLiteConnection
    ) throws -> [AssessmentReason] {
        let reasonSQL = """
            SELECT id, code, title, detail, ingredient, severity
            FROM assessment_reasons
            WHERE assessment_id = ?1
            ORDER BY position ASC, id ASC;
            """

        let statement = try prepare(reasonSQL, connection: connection)
        defer { sqlite3_finalize(statement) }
        guard sqlite3_bind_int64(statement, 1, assessmentID) == SQLITE_OK else {
            throw queryError(connection: connection)
        }

        var result: [AssessmentReason] = []
        while true {
            try Task.checkCancellation()
            switch sqlite3_step(statement) {
            case SQLITE_ROW:
                let rawSeverity = requiredText(statement, column: 5)
                guard let severity = EvidenceSeverity(rawValue: rawSeverity) else {
                    throw ProductCatalogError.invalidRecord(
                        "unsupported reason severity \(rawSeverity)"
                    )
                }
                result.append(
                    AssessmentReason(
                        id: sqlite3_column_int64(statement, 0),
                        code: requiredText(statement, column: 1),
                        title: requiredText(statement, column: 2),
                        detail: requiredText(statement, column: 3),
                        ingredient: optionalText(statement, column: 4),
                        severity: severity
                    )
                )
            case SQLITE_DONE:
                return result
            default:
                throw queryError(connection: connection)
            }
        }
    }

    private func certifications(
        for assessmentID: Int64,
        connection: SQLiteConnection
    ) throws -> [CertificationEvidence] {
        let certificationSQL = """
            SELECT
                c.id,
                c.certifying_body,
                c.certificate_reference,
                c.scope,
                c.valid_from,
                c.valid_until,
                s.name,
                s.kind,
                s.reference,
                s.license,
                s.retrieved_at
            FROM certification_evidence AS c
            JOIN sources AS s ON s.id = c.source_id
            WHERE c.assessment_id = ?1
            ORDER BY c.position ASC, c.id ASC;
            """

        let statement = try prepare(certificationSQL, connection: connection)
        defer { sqlite3_finalize(statement) }
        guard sqlite3_bind_int64(statement, 1, assessmentID) == SQLITE_OK else {
            throw queryError(connection: connection)
        }

        var result: [CertificationEvidence] = []
        while true {
            try Task.checkCancellation()
            switch sqlite3_step(statement) {
            case SQLITE_ROW:
                let validFrom = try optionalDate(statement, column: 4, field: "valid_from")
                let validUntil = try optionalDate(statement, column: 5, field: "valid_until")
                result.append(
                    CertificationEvidence(
                        id: sqlite3_column_int64(statement, 0),
                        certifyingBody: requiredText(statement, column: 1),
                        certificateReference: requiredText(statement, column: 2),
                        scope: requiredText(statement, column: 3),
                        validFrom: validFrom,
                        validUntil: validUntil,
                        source: ProductSource(
                            name: requiredText(statement, column: 6),
                            kind: requiredText(statement, column: 7),
                            reference: requiredText(statement, column: 8),
                            license: requiredText(statement, column: 9),
                            retrievedAt: try parseDate(
                                requiredText(statement, column: 10),
                                field: "certification_source.retrieved_at"
                            )
                        )
                    )
                )
            case SQLITE_DONE:
                return result
            default:
                throw queryError(connection: connection)
            }
        }
    }

    private func prepare(
        _ sql: String,
        connection: SQLiteConnection
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
        connection: SQLiteConnection
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
        guard result == SQLITE_OK else {
            throw queryError(connection: connection)
        }
    }

    private func requiredText(_ statement: OpaquePointer, column: Int32) -> String {
        guard let value = sqlite3_column_text(statement, column) else { return "" }
        return String(cString: value)
    }

    private func optionalText(_ statement: OpaquePointer, column: Int32) -> String? {
        guard sqlite3_column_type(statement, column) != SQLITE_NULL,
              let value = sqlite3_column_text(statement, column) else {
            return nil
        }
        return String(cString: value)
    }

    private func optionalDate(
        _ statement: OpaquePointer,
        column: Int32,
        field: String
    ) throws -> Date? {
        guard let value = optionalText(statement, column: column) else {
            return nil
        }
        return try parseDate(value, field: field)
    }

    private func parseDate(_ value: String, field: String) throws -> Date {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        guard let date = formatter.date(from: value) else {
            throw ProductCatalogError.invalidRecord("\(field) is not ISO-8601: \(value)")
        }
        return date
    }

    private func queryError(connection: SQLiteConnection) -> ProductCatalogError {
        ProductCatalogError.queryFailed(String(cString: sqlite3_errmsg(connection.handle)))
    }

    private static func loadAndValidateManifest(
        manifestURL: URL,
        databaseURL: URL
    ) throws -> CatalogManifest {
        let data: Data
        do {
            data = try Data(contentsOf: manifestURL, options: [.mappedIfSafe])
        } catch {
            throw ProductCatalogError.invalidRecord("catalog manifest cannot be read")
        }

        let manifest: CatalogManifest
        do {
            manifest = try JSONDecoder().decode(CatalogManifest.self, from: data)
        } catch {
            throw ProductCatalogError.invalidRecord("catalog manifest is malformed or incomplete")
        }

        guard manifest.schemaVersion == supportedSchemaVersion else {
            throw ProductCatalogError.incompatibleSchema(
                expected: supportedSchemaVersion,
                actual: manifest.schemaVersion
            )
        }
        guard manifest.recordCount >= 0 else {
            throw ProductCatalogError.invalidRecord("catalog manifest record count is invalid")
        }
        guard isLowercaseSHA256(manifest.sha256) else {
            throw ProductCatalogError.invalidRecord("catalog manifest SHA-256 is invalid")
        }
        guard manifest.sourcePolicy.schemaVersion == supportedSourcePolicySchemaVersion,
              !manifest.sourcePolicy.contractVersion.isEmpty,
              isLowercaseSHA256(manifest.sourcePolicy.sha256) else {
            throw ProductCatalogError.invalidRecord("catalog manifest source-policy identity is invalid")
        }

        let actualDigest: String
        do {
            actualDigest = try sha256(of: databaseURL)
        } catch {
            throw ProductCatalogError.invalidRecord("catalog database digest could not be computed")
        }
        guard actualDigest == manifest.sha256 else {
            throw ProductCatalogError.invalidRecord("catalog database SHA-256 does not match manifest")
        }
        return manifest
    }

    private static func sha256(of url: URL) throws -> String {
        let handle = try FileHandle(forReadingFrom: url)
        defer { try? handle.close() }

        var hasher = SHA256()
        while let data = try handle.read(upToCount: 1024 * 1024), !data.isEmpty {
            hasher.update(data: data)
        }
        return hasher.finalize().map { String(format: "%02x", $0) }.joined()
    }

    private static func isLowercaseSHA256(_ value: String) -> Bool {
        value.utf8.count == 64 && value.utf8.allSatisfy { byte in
            (48...57).contains(byte) || (97...102).contains(byte)
        }
    }

    private static func validateIntegrity(database: OpaquePointer) throws {
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(database, "PRAGMA integrity_check;", -1, &statement, nil) == SQLITE_OK,
              let statement else {
            throw ProductCatalogError.queryFailed(String(cString: sqlite3_errmsg(database)))
        }
        defer { sqlite3_finalize(statement) }

        guard sqlite3_step(statement) == SQLITE_ROW,
              let text = sqlite3_column_text(statement, 0),
              String(cString: text) == "ok",
              sqlite3_step(statement) == SQLITE_DONE else {
            throw ProductCatalogError.invalidRecord("SQLite integrity check failed")
        }

        var foreignKeyStatement: OpaquePointer?
        guard sqlite3_prepare_v2(
            database,
            "PRAGMA foreign_key_check;",
            -1,
            &foreignKeyStatement,
            nil
        ) == SQLITE_OK, let foreignKeyStatement else {
            throw ProductCatalogError.queryFailed(String(cString: sqlite3_errmsg(database)))
        }
        defer { sqlite3_finalize(foreignKeyStatement) }

        guard sqlite3_step(foreignKeyStatement) == SQLITE_DONE else {
            throw ProductCatalogError.invalidRecord("SQLite foreign-key check failed")
        }
    }

    private static func validateRequiredTables(database: OpaquePointer) throws {
        var statement: OpaquePointer?
        let sql = "SELECT name FROM sqlite_master WHERE type = 'table';"
        guard sqlite3_prepare_v2(database, sql, -1, &statement, nil) == SQLITE_OK,
              let statement else {
            throw ProductCatalogError.queryFailed(String(cString: sqlite3_errmsg(database)))
        }
        defer { sqlite3_finalize(statement) }

        var tables: Set<String> = []
        while true {
            switch sqlite3_step(statement) {
            case SQLITE_ROW:
                if let name = sqlite3_column_text(statement, 0) {
                    tables.insert(String(cString: name))
                }
            case SQLITE_DONE:
                let missing = requiredTables.subtracting(tables)
                guard missing.isEmpty else {
                    throw ProductCatalogError.invalidRecord("catalog is missing required SQLite tables")
                }
                return
            default:
                throw ProductCatalogError.queryFailed(String(cString: sqlite3_errmsg(database)))
            }
        }
    }

    private static func execute(_ sql: String, database: OpaquePointer) throws {
        var errorMessage: UnsafeMutablePointer<CChar>?
        let result = sqlite3_exec(database, sql, nil, nil, &errorMessage)
        guard result == SQLITE_OK else {
            let message = errorMessage.map { String(cString: $0) }
                ?? String(cString: sqlite3_errmsg(database))
            sqlite3_free(errorMessage)
            throw ProductCatalogError.queryFailed(message)
        }
    }

    private static func readIntegerPragma(_ name: String, database: OpaquePointer) throws -> Int32 {
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(database, "PRAGMA \(name);", -1, &statement, nil) == SQLITE_OK,
              let statement else {
            throw ProductCatalogError.queryFailed(String(cString: sqlite3_errmsg(database)))
        }
        defer { sqlite3_finalize(statement) }

        guard sqlite3_step(statement) == SQLITE_ROW else {
            throw ProductCatalogError.queryFailed(String(cString: sqlite3_errmsg(database)))
        }
        return sqlite3_column_int(statement, 0)
    }

    private static func readCount(_ sql: String, database: OpaquePointer) throws -> Int {
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(database, sql, -1, &statement, nil) == SQLITE_OK,
              let statement else {
            throw ProductCatalogError.queryFailed(String(cString: sqlite3_errmsg(database)))
        }
        defer { sqlite3_finalize(statement) }

        guard sqlite3_step(statement) == SQLITE_ROW else {
            throw ProductCatalogError.queryFailed(String(cString: sqlite3_errmsg(database)))
        }
        return Int(sqlite3_column_int64(statement, 0))
    }

    private static func readMetadata(_ key: String, database: OpaquePointer) throws -> String {
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(
            database,
            "SELECT value FROM catalog_metadata WHERE key = ?1 LIMIT 1;",
            -1,
            &statement,
            nil
        ) == SQLITE_OK, let statement else {
            throw ProductCatalogError.queryFailed(String(cString: sqlite3_errmsg(database)))
        }
        defer { sqlite3_finalize(statement) }

        let bindResult = key.withCString { pointer in
            sqlite3_bind_text(
                statement,
                1,
                pointer,
                -1,
                unsafeBitCast(-1, to: sqlite3_destructor_type.self)
            )
        }
        guard bindResult == SQLITE_OK, sqlite3_step(statement) == SQLITE_ROW,
              let value = sqlite3_column_text(statement, 0) else {
            throw ProductCatalogError.invalidRecord("missing metadata key \(key)")
        }
        return String(cString: value)
    }
}
