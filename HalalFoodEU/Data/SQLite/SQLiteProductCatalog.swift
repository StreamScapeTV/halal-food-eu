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

    /// Loads one immutable product-detail projection. The repository performs a fixed,
    /// bounded set of statements for the product row and its ordered evidence collections;
    /// SwiftUI never issues per-row/N+1 queries.
    func product(for barcode: Barcode) async throws -> ProductRecord? {
        try Task.checkCancellation()
        let (connection, catalogVersion) = try openIfNeeded()

        let productSQL = """
            SELECT
                p.gtin,
                p.market,
                p.name,
                p.brand,
                p.brand_owner,
                p.quantity,
                p.conflict_flags_json,
                o.id,
                o.ingredients_text,
                o.language_code,
                o.allergens_text,
                o.traces_text,
                o.observed_at,
                o.retrieved_at,
                o.ingredients_hash,
                o.verification_state,
                o.freshness_state,
                s.operator,
                s.source_class,
                s.reference,
                s.license,
                s.attribution,
                s.retrieved_at,
                a.id,
                a.observation_id,
                a.status,
                a.summary,
                a.methodology_version,
                a.assessed_at,
                a.reviewed_at,
                a.approved_reviewer_count,
                a.recheck_at
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
        if stepResult == SQLITE_DONE { return nil }
        guard stepResult == SQLITE_ROW else { throw queryError(connection: connection) }

        let storedBarcode = try Barcode(validating: requiredText(statement, column: 0))
        let market = requiredText(statement, column: 1)
        guard market == "DE" else {
            throw ProductCatalogError.invalidRecord("runtime catalog contains unsupported market \(market)")
        }
        let name = requiredText(statement, column: 2)
        let brand = optionalText(statement, column: 3)
        let brandOwner = optionalText(statement, column: 4)
        let quantity = optionalText(statement, column: 5)
        let conflictFlags = try decodeStringArray(
            requiredText(statement, column: 6),
            field: "products.conflict_flags_json"
        )
        let observationID = optionalInt64(statement, column: 7)

        let observation: IngredientObservation?
        if observationID == nil {
            observation = nil
        } else {
            let ingredientsText = requiredText(statement, column: 8)
            let languageCode = requiredText(statement, column: 9)
            let allergensText = optionalText(statement, column: 10)
            let tracesText = optionalText(statement, column: 11)
            let observedAt = try optionalDate(statement, column: 12, field: "observed_at")
            let observationRetrievedAt = try parseDate(
                requiredText(statement, column: 13),
                field: "product_observations.retrieved_at"
            )
            let ingredientsHash = requiredText(statement, column: 14)
            let rawVerification = requiredText(statement, column: 15)
            guard let verificationState = EvidenceVerificationState(rawValue: rawVerification) else {
                throw ProductCatalogError.invalidRecord(
                    "unsupported ingredient verification state \(rawVerification)"
                )
            }
            let rawFreshness = requiredText(statement, column: 16)
            guard let freshness = EvidenceFreshness(rawValue: rawFreshness) else {
                throw ProductCatalogError.invalidRecord(
                    "unsupported formulation freshness \(rawFreshness)"
                )
            }
            let sourceName = requiredText(statement, column: 17)
            let sourceKind = requiredText(statement, column: 18)
            let sourceReference = requiredText(statement, column: 19)
            let sourceLicense = requiredText(statement, column: 20)
            let sourceAttribution = requiredText(statement, column: 21)
            let sourceRetrievedAt = try parseDate(
                requiredText(statement, column: 22),
                field: "sources.retrieved_at"
            )
            guard !ingredientsText.isEmpty,
                  !languageCode.isEmpty,
                  !ingredientsHash.isEmpty,
                  !sourceName.isEmpty,
                  !sourceKind.isEmpty,
                  !sourceReference.isEmpty,
                  !sourceLicense.isEmpty,
                  !sourceAttribution.isEmpty else {
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
                    retrievedAt: sourceRetrievedAt,
                    attribution: sourceAttribution
                ),
                details: IngredientObservationDetails(
                    allergensText: allergensText,
                    tracesText: tracesText,
                    retrievedAt: observationRetrievedAt,
                    verificationState: verificationState
                )
            )
        }

        let assessment: HalalAssessment
        if let assessmentID = optionalInt64(statement, column: 23) {
            let assessmentObservationID = optionalInt64(statement, column: 24)
            let rawStatus = requiredText(statement, column: 25)
            let summary = requiredText(statement, column: 26)
            let methodologyVersion = requiredText(statement, column: 27)
            let assessedAt = try parseDate(requiredText(statement, column: 28), field: "assessed_at")
            let reviewedAt = try parseDate(requiredText(statement, column: 29), field: "reviewed_at")
            let approvedReviewerCount = Int(sqlite3_column_int(statement, 30))
            let recheckAt = try optionalDate(statement, column: 31, field: "recheck_at")

            guard let status = HalalStatus(rawValue: rawStatus) else {
                throw ProductCatalogError.invalidRecord("unsupported halal status \(rawStatus)")
            }
            guard approvedReviewerCount >= 1 else {
                throw ProductCatalogError.invalidRecord("current assessment has no approved reviewer")
            }
            guard assessmentObservationID == observationID else {
                throw ProductCatalogError.invalidRecord(
                    "current assessment is not bound to the current formulation"
                )
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
                certifications: certifications,
                assessedAt: assessedAt,
                recheckAt: recheckAt,
                approvedReviewerCount: approvedReviewerCount
            )
        } else {
            for column in 24...31 where sqlite3_column_type(statement, Int32(column)) != SQLITE_NULL {
                throw ProductCatalogError.invalidRecord(
                    "unreviewed product unexpectedly exposes assessment columns"
                )
            }
            assessment = .unreviewedUnknown
        }

        let retailerEvidence = try retailerEvidence(
            for: storedBarcode.rawValue,
            connection: connection
        )
        let remoteImages = try remoteImages(
            for: storedBarcode.rawValue,
            connection: connection
        )
        try Task.checkCancellation()

        return ProductRecord(
            barcode: storedBarcode,
            name: name,
            brand: brand,
            observation: observation,
            assessment: assessment,
            catalogVersion: catalogVersion,
            details: ProductRecordDetails(
                market: market,
                brandOwner: brandOwner,
                quantity: quantity,
                conflictFlags: conflictFlags,
                retailerEvidence: retailerEvidence,
                remoteImages: remoteImages
            )
        )
    }

    private func openIfNeeded() throws -> (SQLiteConnection, String) {
        if let connection, let catalogVersion { return (connection, catalogVersion) }

        let manifest = try Self.loadAndValidateManifest(
            manifestURL: manifestURL,
            databaseURL: databaseURL
        )

        var openedDatabase: OpaquePointer?
        let flags = SQLITE_OPEN_READONLY | SQLITE_OPEN_FULLMUTEX
        let openResult = sqlite3_open_v2(databaseURL.path, &openedDatabase, flags, nil)
        guard openResult == SQLITE_OK, let openedDatabase else {
            let message = String(cString: sqlite3_errstr(openResult))
            if let openedDatabase { sqlite3_close(openedDatabase) }
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
        let productCount = try Self.readCount("SELECT COUNT(*) FROM products;", database: openedDatabase)
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
        let sql = """
            SELECT id, code, title, detail, ingredient, severity
            FROM assessment_reasons
            WHERE assessment_id = ?1
            ORDER BY position ASC, id ASC;
            """
        let statement = try prepare(sql, connection: connection)
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
        let sql = """
            SELECT
                c.id,
                c.certifying_body,
                c.scheme,
                c.certificate_reference,
                c.scope,
                c.valid_from,
                c.valid_until,
                c.last_checked_at,
                s.operator,
                s.source_class,
                s.reference,
                s.license,
                s.attribution,
                s.retrieved_at
            FROM certification_evidence AS c
            JOIN sources AS s ON s.id = c.source_id
            WHERE c.assessment_id = ?1
            ORDER BY c.position ASC, c.id ASC;
            """
        let statement = try prepare(sql, connection: connection)
        defer { sqlite3_finalize(statement) }
        guard sqlite3_bind_int64(statement, 1, assessmentID) == SQLITE_OK else {
            throw queryError(connection: connection)
        }

        var result: [CertificationEvidence] = []
        while true {
            try Task.checkCancellation()
            switch sqlite3_step(statement) {
            case SQLITE_ROW:
                result.append(
                    CertificationEvidence(
                        id: sqlite3_column_int64(statement, 0),
                        certifyingBody: requiredText(statement, column: 1),
                        certificateReference: requiredText(statement, column: 3),
                        scope: requiredText(statement, column: 4),
                        validFrom: try optionalDate(statement, column: 5, field: "valid_from"),
                        validUntil: try optionalDate(statement, column: 6, field: "valid_until"),
                        source: ProductSource(
                            name: requiredText(statement, column: 8),
                            kind: requiredText(statement, column: 9),
                            reference: requiredText(statement, column: 10),
                            license: requiredText(statement, column: 11),
                            retrievedAt: try parseDate(
                                requiredText(statement, column: 13),
                                field: "certification_source.retrieved_at"
                            ),
                            attribution: requiredText(statement, column: 12)
                        ),
                        scheme: requiredText(statement, column: 2),
                        lastCheckedAt: try parseDate(
                            requiredText(statement, column: 7),
                            field: "certification.last_checked_at"
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

    private func retailerEvidence(
        for gtin: String,
        connection: SQLiteConnection
    ) throws -> [RetailerEvidence] {
        let sql = """
            SELECT
                r.id,
                r.kind,
                r.retailer_key,
                r.observed_at,
                r.snapshot_at,
                r.scope,
                r.location_id,
                r.limitations,
                s.operator,
                s.source_class,
                s.reference,
                s.license,
                s.attribution,
                s.retrieved_at
            FROM retailer_evidence AS r
            JOIN sources AS s ON s.id = r.source_id
            WHERE r.gtin = ?1
            ORDER BY r.position ASC, r.id ASC;
            """
        let statement = try prepare(sql, connection: connection)
        defer { sqlite3_finalize(statement) }
        try bind(gtin, at: 1, to: statement, connection: connection)

        var result: [RetailerEvidence] = []
        while true {
            try Task.checkCancellation()
            switch sqlite3_step(statement) {
            case SQLITE_ROW:
                let rawKind = requiredText(statement, column: 1)
                guard let kind = RetailerEvidenceKind(rawValue: rawKind) else {
                    throw ProductCatalogError.invalidRecord(
                        "unsupported retailer evidence kind \(rawKind)"
                    )
                }
                let limitations = requiredText(statement, column: 7)
                guard !limitations.isEmpty else {
                    throw ProductCatalogError.invalidRecord("retailer evidence lacks limitations")
                }
                result.append(
                    RetailerEvidence(
                        id: sqlite3_column_int64(statement, 0),
                        kind: kind,
                        retailerKey: requiredText(statement, column: 2),
                        observedAt: try optionalDate(statement, column: 3, field: "retailer.observed_at"),
                        snapshotAt: try optionalDate(statement, column: 4, field: "retailer.snapshot_at"),
                        scope: optionalText(statement, column: 5),
                        locationID: optionalText(statement, column: 6),
                        limitations: limitations,
                        source: ProductSource(
                            name: requiredText(statement, column: 8),
                            kind: requiredText(statement, column: 9),
                            reference: requiredText(statement, column: 10),
                            license: requiredText(statement, column: 11),
                            retrievedAt: try parseDate(
                                requiredText(statement, column: 13),
                                field: "retailer_source.retrieved_at"
                            ),
                            attribution: requiredText(statement, column: 12)
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

    private func remoteImages(
        for gtin: String,
        connection: SQLiteConnection
    ) throws -> [RemoteProductImage] {
        let sql = """
            SELECT
                r.id,
                r.purpose,
                r.url,
                r.image_id,
                r.revision,
                s.operator,
                s.source_class,
                s.reference,
                s.license,
                s.attribution,
                s.retrieved_at
            FROM remote_image_references AS r
            JOIN sources AS s ON s.id = r.source_id
            WHERE r.gtin = ?1
            ORDER BY r.position ASC, r.id ASC;
            """
        let statement = try prepare(sql, connection: connection)
        defer { sqlite3_finalize(statement) }
        try bind(gtin, at: 1, to: statement, connection: connection)

        var result: [RemoteProductImage] = []
        while true {
            try Task.checkCancellation()
            switch sqlite3_step(statement) {
            case SQLITE_ROW:
                let rawPurpose = requiredText(statement, column: 1)
                guard let purpose = RemoteImagePurpose(rawValue: rawPurpose) else {
                    throw ProductCatalogError.invalidRecord(
                        "unsupported remote image purpose \(rawPurpose)"
                    )
                }
                let rawURL = requiredText(statement, column: 2)
                guard let url = URL(string: rawURL),
                      url.scheme?.lowercased() == "https",
                      url.host != nil else {
                    throw ProductCatalogError.invalidRecord("remote image URL is not HTTPS")
                }
                result.append(
                    RemoteProductImage(
                        id: sqlite3_column_int64(statement, 0),
                        purpose: purpose,
                        url: url,
                        imageID: requiredText(statement, column: 3),
                        revision: optionalText(statement, column: 4),
                        source: ProductSource(
                            name: requiredText(statement, column: 5),
                            kind: requiredText(statement, column: 6),
                            reference: requiredText(statement, column: 7),
                            license: requiredText(statement, column: 8),
                            retrievedAt: try parseDate(
                                requiredText(statement, column: 10),
                                field: "remote_image_source.retrieved_at"
                            ),
                            attribution: requiredText(statement, column: 9)
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

    private func prepare(_ sql: String, connection: SQLiteConnection) throws -> OpaquePointer {
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
        guard result == SQLITE_OK else { throw queryError(connection: connection) }
    }

    private func requiredText(_ statement: OpaquePointer, column: Int32) -> String {
        guard let value = sqlite3_column_text(statement, column) else { return "" }
        return String(cString: value)
    }

    private func optionalText(_ statement: OpaquePointer, column: Int32) -> String? {
        guard sqlite3_column_type(statement, column) != SQLITE_NULL,
              let value = sqlite3_column_text(statement, column) else { return nil }
        return String(cString: value)
    }

    private func optionalInt64(_ statement: OpaquePointer, column: Int32) -> Int64? {
        guard sqlite3_column_type(statement, column) != SQLITE_NULL else { return nil }
        return sqlite3_column_int64(statement, column)
    }

    private func optionalDate(
        _ statement: OpaquePointer,
        column: Int32,
        field: String
    ) throws -> Date? {
        guard let value = optionalText(statement, column: column) else { return nil }
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

    private func decodeStringArray(_ value: String, field: String) throws -> [String] {
        guard let data = value.data(using: .utf8),
              let decoded = try? JSONDecoder().decode([String].self, from: data),
              decoded.allSatisfy({ !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }) else {
            throw ProductCatalogError.invalidRecord("\(field) is not a valid string array")
        }
        return decoded
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
                throw ProductCatalogError.invalidRecord("catalog metadata mismatch for \(key)")
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
