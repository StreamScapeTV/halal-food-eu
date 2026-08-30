import CryptoKit
import Foundation
import SQLite3
import Testing
@testable import HalalFoodEU

@Suite("Bundled SQLite catalog")
struct CatalogIntegrationTests {
    @Test("Loads a reviewed production-schema product and exact evidence lineage")
    func loadsKnownProduct() async throws {
        let catalog = try makeCatalog()
        let barcode = try Barcode(validating: "0200000000004")
        let product = try #require(try await catalog.product(for: barcode))

        #expect(product.name == "Demonstration Oat Drink")
        #expect(product.assessment.status == .halalCertified)
        #expect(product.assessment.reasons.map(\.code) == ["SYNTHETIC-CERTIFICATE-MATCH"])
        #expect(product.assessment.certifications.count == 1)
        #expect(product.observation?.freshness == .current)
        #expect(product.observation?.observedAt != nil)
        #expect(product.catalogVersion == "0.2.0-demo.1")
    }

    @Test("A known product without reviewed formulation remains unknown instead of disappearing")
    func loadsKnownUnknownWithoutIngredientEvidence() async throws {
        let fixture = try makeTemporaryCatalogFixture()
        defer { try? FileManager.default.removeItem(at: fixture.directory) }

        try executeSQLite(
            """
            UPDATE product_assessments
            SET observation_id = NULL, status = 'unknown'
            WHERE id = (
                SELECT current_assessment_id FROM products WHERE gtin = '00200000000028'
            );
            UPDATE products
            SET current_observation_id = NULL
            WHERE gtin = '00200000000028';
            """,
            databaseURL: fixture.database
        )
        try refreshManifestDigest(databaseURL: fixture.database, manifestURL: fixture.manifest)

        let catalog = SQLiteProductCatalog(
            databaseURL: fixture.database,
            manifestURL: fixture.manifest
        )
        let barcode = try Barcode(validating: "00200000000028")
        let product = try #require(try await catalog.product(for: barcode))

        #expect(product.assessment.status == .unknown)
        #expect(product.observation == nil)
    }

    @Test("A valid absent GTIN returns nil instead of an unknown product")
    func returnsNilForAbsentProduct() async throws {
        let catalog = try makeCatalog()
        let barcode = try Barcode(validating: "0200000000035")
        let product = try await catalog.product(for: barcode)
        #expect(product == nil)
    }

    @Test("A database whose bytes do not match the bundled manifest fails closed")
    func rejectsDatabaseDigestTampering() async throws {
        let fixture = try makeTemporaryCatalogFixture()
        defer { try? FileManager.default.removeItem(at: fixture.directory) }

        var bytes = try Data(contentsOf: fixture.database)
        #expect(!bytes.isEmpty)
        bytes[bytes.startIndex] ^= 0x01
        try bytes.write(to: fixture.database, options: .atomic)

        let catalog = SQLiteProductCatalog(
            databaseURL: fixture.database,
            manifestURL: fixture.manifest
        )
        let barcode = try Barcode(validating: "0200000000004")

        do {
            _ = try await catalog.product(for: barcode)
            Issue.record("Tampered database bytes must not be readable")
        } catch ProductCatalogError.invalidRecord(let message) {
            #expect(message.contains("SHA-256"))
        } catch {
            Issue.record("Expected a catalog integrity error, got \(error)")
        }
    }

    @Test("Unsupported SQLite schema metadata fails after digest validation")
    func rejectsUnsupportedSQLiteSchemaVersion() async throws {
        let fixture = try makeTemporaryCatalogFixture()
        defer { try? FileManager.default.removeItem(at: fixture.directory) }

        try executeSQLite("PRAGMA user_version = 999;", databaseURL: fixture.database)
        try refreshManifestDigest(databaseURL: fixture.database, manifestURL: fixture.manifest)

        let catalog = SQLiteProductCatalog(
            databaseURL: fixture.database,
            manifestURL: fixture.manifest
        )
        let barcode = try Barcode(validating: "0200000000004")

        do {
            _ = try await catalog.product(for: barcode)
            Issue.record("Unsupported SQLite schema metadata must fail closed")
        } catch ProductCatalogError.incompatibleSchema(let expected, let actual) {
            #expect(expected == 2)
            #expect(actual == 999)
        } catch {
            Issue.record("Expected an incompatible schema error, got \(error)")
        }
    }

    @Test("Unexpected SQLite application identifiers fail after digest validation")
    func rejectsUnexpectedApplicationIdentifier() async throws {
        let fixture = try makeTemporaryCatalogFixture()
        defer { try? FileManager.default.removeItem(at: fixture.directory) }

        try executeSQLite("PRAGMA application_id = 0;", databaseURL: fixture.database)
        try refreshManifestDigest(databaseURL: fixture.database, manifestURL: fixture.manifest)

        let catalog = SQLiteProductCatalog(
            databaseURL: fixture.database,
            manifestURL: fixture.manifest
        )
        let barcode = try Barcode(validating: "0200000000004")

        do {
            _ = try await catalog.product(for: barcode)
            Issue.record("Unexpected SQLite application identifiers must fail closed")
        } catch ProductCatalogError.invalidRecord(let message) {
            #expect(message.contains("application identifier"))
        } catch {
            Issue.record("Expected an invalid catalog record error, got \(error)")
        }
    }

    @Test("Missing required SQLite tables fail after digest validation")
    func rejectsMissingRequiredTable() async throws {
        let fixture = try makeTemporaryCatalogFixture()
        defer { try? FileManager.default.removeItem(at: fixture.directory) }

        try executeSQLite("DROP TABLE assessment_reasons;", databaseURL: fixture.database)
        try refreshManifestDigest(databaseURL: fixture.database, manifestURL: fixture.manifest)

        let catalog = SQLiteProductCatalog(
            databaseURL: fixture.database,
            manifestURL: fixture.manifest
        )
        let barcode = try Barcode(validating: "0200000000004")

        do {
            _ = try await catalog.product(for: barcode)
            Issue.record("Catalogs missing required tables must fail closed")
        } catch ProductCatalogError.invalidRecord(let message) {
            #expect(message.contains("missing required SQLite tables"))
        } catch {
            Issue.record("Expected an invalid catalog record error, got \(error)")
        }
    }

    @Test("Foreign-key violations fail after digest validation")
    func rejectsForeignKeyViolations() async throws {
        let fixture = try makeTemporaryCatalogFixture()
        defer { try? FileManager.default.removeItem(at: fixture.directory) }

        try executeSQLite(
            """
            PRAGMA foreign_keys = OFF;
            UPDATE products SET current_observation_id = 999999 WHERE gtin = '00200000000004';
            """,
            databaseURL: fixture.database
        )
        #expect(try containsForeignKeyViolation(databaseURL: fixture.database))
        try refreshManifestDigest(databaseURL: fixture.database, manifestURL: fixture.manifest)
        #expect(try manifestDigestMatches(databaseURL: fixture.database, manifestURL: fixture.manifest))

        let catalog = SQLiteProductCatalog(
            databaseURL: fixture.database,
            manifestURL: fixture.manifest
        )
        let barcode = try Barcode(validating: "0200000000004")

        do {
            _ = try await catalog.product(for: barcode)
            Issue.record("Catalogs with foreign-key violations must fail closed")
        } catch ProductCatalogError.invalidRecord {
            // The fixture proves a digest-matched foreign-key violation above. SQLite may
            // surface it through either integrity validation path across system versions.
        } catch {
            Issue.record("Expected an invalid catalog record error, got \(error)")
        }
    }

    @Test("An unsupported manifest envelope schema fails before SQLite reads")
    func rejectsUnsupportedManifestEnvelopeSchema() async throws {
        let fixture = try makeTemporaryCatalogFixture()
        defer { try? FileManager.default.removeItem(at: fixture.directory) }

        try mutateManifest(at: fixture.manifest) { manifest in
            manifest["manifestSchemaVersion"] = 999
        }

        let catalog = SQLiteProductCatalog(
            databaseURL: fixture.database,
            manifestURL: fixture.manifest
        )
        let barcode = try Barcode(validating: "0200000000004")

        do {
            _ = try await catalog.product(for: barcode)
            Issue.record("Unsupported manifest envelope schemas must fail closed")
        } catch ProductCatalogError.invalidRecord(let message) {
            #expect(message.contains("manifest schema"))
        } catch {
            Issue.record("Expected an invalid manifest error, got \(error)")
        }
    }

    @Test("An unsupported catalog schema in the manifest fails before SQLite reads")
    func rejectsUnsupportedManifestCatalogSchema() async throws {
        let fixture = try makeTemporaryCatalogFixture()
        defer { try? FileManager.default.removeItem(at: fixture.directory) }

        try mutateManifest(at: fixture.manifest) { manifest in
            manifest["schemaVersion"] = 999
        }

        let catalog = SQLiteProductCatalog(
            databaseURL: fixture.database,
            manifestURL: fixture.manifest
        )
        let barcode = try Barcode(validating: "0200000000004")

        do {
            _ = try await catalog.product(for: barcode)
            Issue.record("Unsupported manifest catalog schemas must fail closed")
        } catch ProductCatalogError.incompatibleSchema(let expected, let actual) {
            #expect(expected == 2)
            #expect(actual == 999)
        } catch {
            Issue.record("Expected an incompatible schema error, got \(error)")
        }
    }

    @Test("Source-policy lineage must match the exact SQLite source rows")
    func rejectsSourcePolicyLineageMismatch() async throws {
        let fixture = try makeTemporaryCatalogFixture()
        defer { try? FileManager.default.removeItem(at: fixture.directory) }

        try mutateManifest(at: fixture.manifest) { manifest in
            guard var policies = manifest["sourcePolicies"] as? [[String: Any]], !policies.isEmpty else {
                throw CocoaError(.fileReadCorruptFile)
            }
            policies[0]["sha256"] = String(repeating: "0", count: 64)
            manifest["sourcePolicies"] = policies
        }

        let catalog = SQLiteProductCatalog(
            databaseURL: fixture.database,
            manifestURL: fixture.manifest
        )
        let barcode = try Barcode(validating: "0200000000004")

        do {
            _ = try await catalog.product(for: barcode)
            Issue.record("Source-policy lineage mismatches must fail closed")
        } catch ProductCatalogError.invalidRecord(let message) {
            #expect(message.contains("source-policy"))
        } catch {
            Issue.record("Expected an invalid source-policy binding error, got \(error)")
        }
    }

    @Test("Quality policy lineage must match SQLite metadata")
    func rejectsQualityPolicyLineageMismatch() async throws {
        let fixture = try makeTemporaryCatalogFixture()
        defer { try? FileManager.default.removeItem(at: fixture.directory) }

        try mutateManifest(at: fixture.manifest) { manifest in
            guard var quality = manifest["qualityGate"] as? [String: Any] else {
                throw CocoaError(.fileReadCorruptFile)
            }
            quality["policySha256"] = String(repeating: "0", count: 64)
            manifest["qualityGate"] = quality
        }

        let catalog = SQLiteProductCatalog(
            databaseURL: fixture.database,
            manifestURL: fixture.manifest
        )
        let barcode = try Barcode(validating: "0200000000004")

        do {
            _ = try await catalog.product(for: barcode)
            Issue.record("Quality-policy lineage mismatches must fail closed")
        } catch ProductCatalogError.invalidRecord(let message) {
            #expect(message.contains("qualityPolicySha256"))
        } catch {
            Issue.record("Expected an invalid quality-gate binding error, got \(error)")
        }
    }

    private func makeCatalog() throws -> SQLiteProductCatalog {
        let bundle = Bundle(for: TestBundleToken.self)
        let databaseURL = try #require(
            bundle.url(forResource: "catalog", withExtension: "sqlite3"),
            "catalog.sqlite3 must be copied into the unit-test bundle"
        )
        let manifestURL = try #require(
            bundle.url(forResource: "catalog-manifest", withExtension: "json"),
            "catalog-manifest.json must be copied into the unit-test bundle"
        )
        return SQLiteProductCatalog(
            databaseURL: databaseURL,
            manifestURL: manifestURL
        )
    }

    private func makeTemporaryCatalogFixture() throws -> (
        directory: URL,
        database: URL,
        manifest: URL
    ) {
        let bundle = Bundle(for: TestBundleToken.self)
        let sourceDatabase = try #require(
            bundle.url(forResource: "catalog", withExtension: "sqlite3")
        )
        let sourceManifest = try #require(
            bundle.url(forResource: "catalog-manifest", withExtension: "json")
        )

        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("HalalFoodEU-Catalog-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)

        let database = directory.appendingPathComponent("catalog.sqlite3")
        let manifest = directory.appendingPathComponent("catalog-manifest.json")
        try FileManager.default.copyItem(at: sourceDatabase, to: database)
        try FileManager.default.copyItem(at: sourceManifest, to: manifest)
        return (directory, database, manifest)
    }

    private func executeSQLite(_ sql: String, databaseURL: URL) throws {
        var database: OpaquePointer?
        let openResult = sqlite3_open_v2(
            databaseURL.path,
            &database,
            SQLITE_OPEN_READWRITE | SQLITE_OPEN_FULLMUTEX,
            nil
        )
        guard openResult == SQLITE_OK, let database else {
            if let database {
                sqlite3_close(database)
            }
            throw CocoaError(.fileReadCorruptFile)
        }
        defer { sqlite3_close(database) }

        guard sqlite3_exec(database, sql, nil, nil, nil) == SQLITE_OK else {
            throw CocoaError(.fileWriteUnknown)
        }
    }

    private func containsForeignKeyViolation(databaseURL: URL) throws -> Bool {
        var database: OpaquePointer?
        let openResult = sqlite3_open_v2(
            databaseURL.path,
            &database,
            SQLITE_OPEN_READONLY | SQLITE_OPEN_FULLMUTEX,
            nil
        )
        guard openResult == SQLITE_OK, let database else {
            if let database {
                sqlite3_close(database)
            }
            throw CocoaError(.fileReadCorruptFile)
        }
        defer { sqlite3_close(database) }

        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(database, "PRAGMA foreign_key_check;", -1, &statement, nil) == SQLITE_OK,
              let statement else {
            throw CocoaError(.fileReadCorruptFile)
        }
        defer { sqlite3_finalize(statement) }
        return sqlite3_step(statement) == SQLITE_ROW
    }

    private func manifestDigestMatches(databaseURL: URL, manifestURL: URL) throws -> Bool {
        let digest = SHA256.hash(data: try Data(contentsOf: databaseURL))
            .map { String(format: "%02x", $0) }
            .joined()
        let manifestData = try Data(contentsOf: manifestURL)
        let manifest = try #require(
            JSONSerialization.jsonObject(with: manifestData) as? [String: Any],
            "catalog manifest must be a JSON object"
        )
        return manifest["sha256"] as? String == digest
    }

    private func refreshManifestDigest(databaseURL: URL, manifestURL: URL) throws {
        let digest = SHA256.hash(data: try Data(contentsOf: databaseURL))
            .map { String(format: "%02x", $0) }
            .joined()
        try mutateManifest(at: manifestURL) { manifest in
            manifest["sha256"] = digest
        }
    }

    private func mutateManifest(
        at url: URL,
        mutation: (inout [String: Any]) throws -> Void
    ) throws {
        let data = try Data(contentsOf: url)
        var manifest = try #require(
            JSONSerialization.jsonObject(with: data) as? [String: Any],
            "catalog manifest must be a JSON object"
        )
        try mutation(&manifest)
        let updated = try JSONSerialization.data(
            withJSONObject: manifest,
            options: [.prettyPrinted, .sortedKeys]
        )
        try updated.write(to: url, options: .atomic)
    }
}

private final class TestBundleToken {}
