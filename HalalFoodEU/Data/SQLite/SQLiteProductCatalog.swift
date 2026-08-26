import Foundation
import SQLite3

actor SQLiteProductCatalog: ProductCatalog {
    static let supportedSchemaVersion = 1
    static let expectedApplicationID: Int32 = 1_212_564_821 // ASCII "HFEU"

    private let database: OpaquePointer
    private let catalogVersion: String

    init(databaseURL: URL) throws {
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

        do {
            try Self.execute("PRAGMA query_only = ON;", database: openedDatabase)
            try Self.execute("PRAGMA foreign_keys = ON;", database: openedDatabase)

            let applicationID = try Self.readIntegerPragma("application_id", database: openedDatabase)
            guard applicationID == Self.expectedApplicationID else {
                throw ProductCatalogError.invalidRecord("unexpected SQLite application identifier \(applicationID)")
            }

            let schemaVersion = try Self.readIntegerPragma("user_version", database: openedDatabase)
            guard schemaVersion == Self.supportedSchemaVersion else {
                throw ProductCatalogError.incompatibleSchema(
                    expected: Self.supportedSchemaVersion,
                    actual: Int(schemaVersion)
                )
            }

            catalogVersion = try Self.readMetadata("catalogVersion", database: openedDatabase)
            database = openedDatabase
        } catch {
            sqlite3_close(openedDatabase)
            throw error
        }
    }

    deinit {
        sqlite3_close(database)
    }

    func product(for barcode: Barcode) async throws -> ProductRecord? {
        try Task.checkCancellation()

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

        let statement = try prepare(productSQL)
        defer { sqlite3_finalize(statement) }

        try bind(barcode.rawValue, at: 1, to: statement)

        let stepResult = sqlite3_step(statement)
        if stepResult == SQLITE_DONE {
            return nil
        }
        guard stepResult == SQLITE_ROW else {
            throw queryError()
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

        let reasons = try reasons(for: assessmentID)
        guard !reasons.isEmpty else {
            throw ProductCatalogError.invalidRecord("assessment \(assessmentID) has no reasons")
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
                reasons: reasons
            ),
            catalogVersion: catalogVersion
        )
    }

    private func reasons(for assessmentID: Int64) throws -> [AssessmentReason] {
        let reasonSQL = """
            SELECT id, code, title, detail, ingredient, severity
            FROM assessment_reasons
            WHERE assessment_id = ?1
            ORDER BY position ASC, id ASC;
            """

        let statement = try prepare(reasonSQL)
        defer { sqlite3_finalize(statement) }
        guard sqlite3_bind_int64(statement, 1, assessmentID) == SQLITE_OK else {
            throw queryError()
        }

        var result: [AssessmentReason] = []
        while true {
            try Task.checkCancellation()
            switch sqlite3_step(statement) {
            case SQLITE_ROW:
                let rawSeverity = requiredText(statement, column: 5)
                guard let severity = EvidenceSeverity(rawValue: rawSeverity) else {
                    throw ProductCatalogError.invalidRecord("unsupported reason severity \(rawSeverity)")
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
                throw queryError()
            }
        }
    }

    private func prepare(_ sql: String) throws -> OpaquePointer {
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(database, sql, -1, &statement, nil) == SQLITE_OK,
              let statement else {
            throw queryError()
        }
        return statement
    }

    private func bind(_ value: String, at index: Int32, to statement: OpaquePointer) throws {
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
            throw queryError()
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

    private func parseDate(_ value: String, field: String) throws -> Date {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        guard let date = formatter.date(from: value) else {
            throw ProductCatalogError.invalidRecord("\(field) is not ISO-8601: \(value)")
        }
        return date
    }

    private func queryError() -> ProductCatalogError {
        ProductCatalogError.queryFailed(String(cString: sqlite3_errmsg(database)))
    }

    private static func execute(_ sql: String, database: OpaquePointer) throws {
        var errorMessage: UnsafeMutablePointer<CChar>?
        let result = sqlite3_exec(database, sql, nil, nil, &errorMessage)
        guard result == SQLITE_OK else {
            let message = errorMessage.map(String.init(cString:)) ?? String(cString: sqlite3_errmsg(database))
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
