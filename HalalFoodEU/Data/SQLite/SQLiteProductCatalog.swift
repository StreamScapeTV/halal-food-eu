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
    struct QualityGate: Decodable, Sendable {
        let schemaVersion: Int
        let policyVersion: String
        let policySha256: String
        let reportSha256: String
        let reportFileSha256: String
        let sourceKey: String
        let snapshotID: String
        let evaluatedAt: String
        let warningCount: Int
    }

    struct SourcePolicy: Decodable, Sendable {
        let sourceKey: String
        let path: String
        let sha256: String
        let schemaVersion: Int
        let license: String
        let attribution: String
    }

    let manifestSchemaVersion: Int
    let catalogVersion: String
    let schemaVersion: Int
    let methodologyVersion: String
    let selectionPolicyVersion: String
    let recordCount: Int
    let sha256: String
    let qualityGate: QualityGate
    let sourcePolicies: [SourcePolicy]
}

actor SQLiteProductCatalog: ProductCatalog {
    static let supportedManifestSchemaVersion = 3
    static let supportedSchemaVersion = 2
    static let supportedSourcePolicySchemaVersion = 1
    static let supportedQualityGateSchemaVersion = 1
    static let expectedApplicationID: Int32 = 1_212_564_821 // ASCII "HFEU"

    private static let requiredTables: Set<String> = [
        "catalog_metadata",
        "sources",
        "products",
        "product_observations",
        "product_assessments",
        "certification_evidence",
        "assessment_reasons",
        "retailer_evidence",
        "remote_image_references",
        "basic_exclusions",
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
                p.market,
                p.name,
                p.brand,
                o.id,
                o.ingredients_text,
                o.language_code,
                o.observed_at,
                o.ingredients_hash,
                o.freshness_state,
                s.operator,
                s.source_class,
                s.reference,
                s.license,
                s.retrieved_at,
                a.id,
                a.observation_id,
                a.status,
                a.summary,
                a.methodology_version,
                a.reviewed_at
            FROM products AS p
            LEFT JOIN product_assessments AS a ON a.id = p.current_assessment_id
            LEFT JOIN product_observations AS o ON o.id = p.current_observation_id
            LEFT JOIN sources AS s ON s.id = o.source_id
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
        let market = requiredText(statement, column: 1)
        guard market == "DE" else {
            throw ProductCatalogError.invalidRecord("runtime catalog contains unsupported market \(market)")
        }
        let name = requiredText(statement, column: 2)
        let brand = optionalText(statement, column: 3)
        let observationID = optionalInt64(statement, column: 4)

        let observation: IngredientObservation?
        if observationID == nil {
            observation = nil
        } else {
            let ingredientsText = requiredText(statement, column: 5)
            let languageCode = requiredText(statement, column: 6)
            let observedAt = try optionalDate(statement, column: 7, field: "observed_at")
            let ingredientsHash = requiredText(statement, column: 8)
            let rawFreshness = requiredText(statement, column: 9)
            guard let freshness = EvidenceFreshness(rawValue: rawFreshness) else {
                throw ProductCatalogError.invalidRecord(
                    "unsupported formulation freshness \(rawFreshness)"
                )
            }
            let sourceName = requiredText(statement, column: 10)
            let sourceKind = requiredText(statement, column: 11)
            let sourceReference = requiredText(statement, column: 12)
            let sourceLicense = requiredText(statement, column: 13)
            let retrievedAt = try parseDate(
                requiredText(statement, column: 14),
                field: "retrieved_at"
            )
            guard !ingredientsText.isEmpty,
                  !languageCode.isEmpty,
                  !ingredientsHash.isEmpty,
                  !sourceName.isEmpty,
                  !sourceKind.isEmpty,
                  !sourceReference.isEmpty,
                  !sourceLicense.isEmpty else {
                throw ProductCatalogError.invalidRecord("ingredient evidence is incomplete")
            }
            observation = IngredientObservation(
                text: ingredientsText,
                languageCode: languageCode,
                observedAt: observedAt,
                contentHash: ingredientsHash,
                freshness: freshness,
                source: ProductSource(
                    name: sourceName,
                    kind: sourceKind,
                    reference: sourceReference,
                    license: sourceLicense,
                    retrievedAt: retrievedAt
                )
            )
        }

        let assessment: HalalAssessment
        if let assessmentID = optionalInt64(statement, column: 15) {
            let assessmentObservationID = optionalInt64(statement, column: 16)
            let rawStatus = requiredText(statement, column: 17)
            let summary = requiredText(statement, column: 18)
            let methodologyVersion = requiredText(statement, column: 19)
            let reviewedAt = try parseDate(requiredText(statement, column: 20), field: "reviewed_at")

            guard let status = HalalStatus(rawValue: rawStatus) else {
                throw ProductCatalogError.invalidRecord("unsupported halal status \(rawStatus)")
            }
            guard assessmentObservationID == observationID else {
                throw ProductCatalogError.invalidRecord("current assessment is not bound to the current formulation")
            }
            if observationID == nil, status != .unknown {
                throw ProductCatalogError.invalidRecord(
                    "product without ingredient evidence has non-unknown status \(rawStatus)"
                )
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
            if status == .notHalal, !reasons.contains(where: { $0.severity == .prohibitive }) {
                throw ProductCatalogError.invalidRecord(
                    "not-halal assessment \(assessmentID) has no prohibitive reason"
                )
            }

            assessment = HalalAssessment(
                status: status,
                summary: summary,
                methodologyVersion: methodologyVersion,
                reviewedAt: reviewedAt,
                reasons: reasons,
                certifications: certifications
            )
        } else {
            guard sqlite3_column_type(statement, 16) == SQLITE_NULL,
                  sqlite3_column_type(statement, 17) == SQLITE_NULL,
                  sqlite3_column_type(statement, 18) == SQLITE_NULL,
                  sqlite3_column_type(statement, 19) == SQLITE_NULL,
                  sqlite3_column_type(statement, 20) == SQLITE_NULL else {
                throw ProductCatalogError.invalidRecord(
                    "unreviewed product unexpectedly exposes assessment columns"
                )
            }
            assessment = .unreviewedUnknown
        }

        return ProductRecord(
            barcode: storedBarcode,
            name: name,
            brand: brand,
            observation: observation,
            assessment: assessment,
            catalogVersion: catalogVersion
        )
    }

    private func openIfNeeded() throws -> (SQLiteConnection, String) {
        if let connection, let catalogVersion {
            return (connection, catalogVersion)
        }

        let manifest = try Self.loadAndValidateManifest(
            manifestURL: manifestURL,
            databaseURL: databaseURL
        )

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
        try Self.validateMetadata(manifest: manifest, database: openedDatabase)
        try Self.validateSourcePolicies(manifest: manifest, database: openedDatabase)

        let openedCatalogVersion = try Self.readMetadata("catalogVersion", database: openedDatabase)
        let productCount = try Self.readCount(
            "SELECT COUNT(*) FROM products;",
            database: openedDatabase
        )
        guard productCount == manifest.recordCount else {
            throw ProductCatalogError.invalidRecord("catalog manifest record count differs from SQLite")
        }
        guard try Self.readCount(
            """
            SELECT COUNT(*)
            FROM products AS p
            JOIN product_assessments AS a ON a.id = p.current_assessment_id
            WHERE NOT (a.observation_id IS p.current_observation_id);
            """,
            database: openedDatabase
        ) == 0 else {
            throw ProductCatalogError.invalidRecord(
                "catalog contains a current assessment not bound to the current formulation"
            )
        }
        guard try Self.readCount(
            """
            SELECT COUNT(*)
            FROM products AS p
            JOIN product_assessments AS a ON a.id = p.current_assessment_id
            WHERE p.current_observation_id IS NULL AND a.status <> 'unknown';
            """,
            database: openedDatabase
        ) == 0 else {
            throw ProductCatalogError.invalidRecord(
                "catalog contains a non-unknown product without ingredient evidence"
            )
        }
        guard try Self.readCount(
            """
            SELECT COUNT(*)
            FROM basic_exclusions AS b
            JOIN products AS p ON p.gtin = b.gtin AND p.market = b.market;
            """,
            database: openedDatabase
        ) == 0 else {
            throw ProductCatalogError.invalidRecord("basic exclusions overlap detailed products")
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
                s.operator,
                s.source_class,
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

    private func optionalInt64(_ statement: OpaquePointer, column: Int32) -> Int64? {
        guard sqlite3_column_type(statement, column) != SQLITE_NULL else {
            return nil
        }
        return sqlite3_column_int64(statement, column)
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

        guard manifest.manifestSchemaVersion == supportedManifestSchemaVersion else {
            throw ProductCatalogError.invalidRecord(
                "unsupported catalog manifest schema \(manifest.manifestSchemaVersion)"
            )
        }
        guard manifest.schemaVersion == supportedSchemaVersion else {
            throw ProductCatalogError.incompatibleSchema(
                expected: supportedSchemaVersion,
                actual: manifest.schemaVersion
            )
        }
        guard manifest.recordCount >= 0,
              !manifest.catalogVersion.isEmpty,
              !manifest.methodologyVersion.isEmpty,
              !manifest.selectionPolicyVersion.isEmpty else {
            throw ProductCatalogError.invalidRecord("catalog manifest identity is invalid")
        }
        guard isLowercaseSHA256(manifest.sha256) else {
            throw ProductCatalogError.invalidRecord("catalog manifest SHA-256 is invalid")
        }

        let quality = manifest.qualityGate
        guard quality.schemaVersion == supportedQualityGateSchemaVersion,
              !quality.policyVersion.isEmpty,
              !quality.sourceKey.isEmpty,
              !quality.snapshotID.isEmpty,
              !quality.evaluatedAt.isEmpty,
              quality.warningCount >= 0,
              isLowercaseSHA256(quality.policySha256),
              isLowercaseSHA256(quality.reportSha256),
              isLowercaseSHA256(quality.reportFileSha256) else {
            throw ProductCatalogError.invalidRecord("catalog manifest quality-gate identity is invalid")
        }

        guard !manifest.sourcePolicies.isEmpty else {
            throw ProductCatalogError.invalidRecord("catalog manifest source-policy identity is missing")
        }
        var sourceKeys: Set<String> = []
        for policy in manifest.sourcePolicies {
            guard policy.schemaVersion == supportedSourcePolicySchemaVersion,
                  !policy.sourceKey.isEmpty,
                  !policy.path.isEmpty,
                  !policy.license.isEmpty,
                  !policy.attribution.isEmpty,
                  isLowercaseSHA256(policy.sha256),
                  sourceKeys.insert(policy.sourceKey).inserted else {
                throw ProductCatalogError.invalidRecord("catalog manifest source-policy identity is invalid")
            }
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

    private static func validateMetadata(
        manifest: CatalogManifest,
        database: OpaquePointer
    ) throws {
        let expected = [
            "catalogVersion": manifest.catalogVersion,
            "schemaVersion": String(manifest.schemaVersion),
            "methodologyVersion": manifest.methodologyVersion,
            "selectionPolicyVersion": manifest.selectionPolicyVersion,
            "qualityPolicyVersion": manifest.qualityGate.policyVersion,
            "qualityPolicySha256": manifest.qualityGate.policySha256,
            "qualityReportSha256": manifest.qualityGate.reportSha256,
            "qualityEvaluatedAt": manifest.qualityGate.evaluatedAt,
        ]
        for (key, value) in expected {
            guard try readMetadata(key, database: database) == value else {
                throw ProductCatalogError.invalidRecord(
                    "catalog metadata mismatch for \(key)"
                )
            }
        }
    }

    private static func validateSourcePolicies(
        manifest: CatalogManifest,
        database: OpaquePointer
    ) throws {
        let expected = Dictionary(
            uniqueKeysWithValues: manifest.sourcePolicies.map { ($0.sourceKey, $0) }
        )
        var statement: OpaquePointer?
        let sql = """
            SELECT source_key, license, attribution, policy_schema_version, policy_sha256
            FROM sources
            ORDER BY source_key;
            """
        guard sqlite3_prepare_v2(database, sql, -1, &statement, nil) == SQLITE_OK,
              let statement else {
            throw ProductCatalogError.queryFailed(String(cString: sqlite3_errmsg(database)))
        }
        defer { sqlite3_finalize(statement) }

        var seen: Set<String> = []
        while true {
            switch sqlite3_step(statement) {
            case SQLITE_ROW:
                guard let keyPointer = sqlite3_column_text(statement, 0),
                      let licensePointer = sqlite3_column_text(statement, 1),
                      let attributionPointer = sqlite3_column_text(statement, 2),
                      let digestPointer = sqlite3_column_text(statement, 4) else {
                    throw ProductCatalogError.invalidRecord("source-policy binding is incomplete")
                }
                let key = String(cString: keyPointer)
                guard let policy = expected[key],
                      String(cString: licensePointer) == policy.license,
                      String(cString: attributionPointer) == policy.attribution,
                      sqlite3_column_int(statement, 3) == Int32(policy.schemaVersion),
                      String(cString: digestPointer) == policy.sha256 else {
                    throw ProductCatalogError.invalidRecord(
                        "source-policy binding differs from catalog manifest"
                    )
                }
                seen.insert(key)
            case SQLITE_DONE:
                guard seen == Set(expected.keys) else {
                    throw ProductCatalogError.invalidRecord(
                        "catalog manifest and SQLite source-policy sets differ"
                    )
                }
                return
            default:
                throw ProductCatalogError.queryFailed(String(cString: sqlite3_errmsg(database)))
            }
        }
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
