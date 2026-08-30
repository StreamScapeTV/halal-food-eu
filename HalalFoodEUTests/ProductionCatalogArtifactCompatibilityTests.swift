import Foundation
import SQLite3
import Testing
@testable import HalalFoodEU

@Suite("Production catalog artifact compatibility")
struct ProductionCatalogArtifactCompatibilityTests {
    @Test("Exact proposed SQLite and manifest load through the runtime repository")
    func loadsExactProposedCatalog() async throws {
        let fixture = try bundledCatalog()
        let gtin = try #require(
            firstGTIN(databaseURL: fixture.database),
            "A production catalog proposed for release must contain at least one product."
        )
        let catalogVersion = try manifestCatalogVersion(manifestURL: fixture.manifest)
        let catalog = SQLiteProductCatalog(
            databaseURL: fixture.database,
            manifestURL: fixture.manifest
        )
        let barcode = try Barcode(validating: gtin)
        let product = try #require(try await catalog.product(for: barcode))

        #expect(product.barcode.rawValue == gtin)
        #expect(product.catalogVersion == catalogVersion)
    }

    @Test("Missing production evidence remains absent or unknown when present")
    func preservesTruthfulMissingEvidence() async throws {
        let fixture = try bundledCatalog()
        let catalog = SQLiteProductCatalog(
            databaseURL: fixture.database,
            manifestURL: fixture.manifest
        )

        if let gtin = try firstGTIN(
            databaseURL: fixture.database,
            predicate: "current_observation_id IS NULL"
        ) {
            let product = try #require(
                try await catalog.product(for: Barcode(validating: gtin))
            )
            #expect(product.observation == nil)
        }

        if let gtin = try firstGTIN(
            databaseURL: fixture.database,
            predicate: "current_assessment_id IS NULL"
        ) {
            let product = try #require(
                try await catalog.product(for: Barcode(validating: gtin))
            )
            #expect(product.assessment.status == .unknown)
            #expect(product.assessment.methodologyVersion == nil)
            #expect(product.assessment.reviewedAt == nil)
        }
    }

    private func bundledCatalog() throws -> (database: URL, manifest: URL) {
        let bundle = Bundle(for: TestBundleToken.self)
        let database = try #require(
            bundle.url(forResource: "catalog", withExtension: "sqlite3"),
            "catalog.sqlite3 must be copied into the unit-test bundle"
        )
        let manifest = try #require(
            bundle.url(forResource: "catalog-manifest", withExtension: "json"),
            "catalog-manifest.json must be copied into the unit-test bundle"
        )
        return (database, manifest)
    }

    private func manifestCatalogVersion(manifestURL: URL) throws -> String {
        let raw = try Data(contentsOf: manifestURL)
        let object = try #require(
            try JSONSerialization.jsonObject(with: raw) as? [String: Any]
        )
        return try #require(object["catalogVersion"] as? String)
    }

    private func firstGTIN(
        databaseURL: URL,
        predicate: String? = nil
    ) throws -> String? {
        var database: OpaquePointer?
        guard sqlite3_open_v2(
            databaseURL.path,
            &database,
            SQLITE_OPEN_READONLY | SQLITE_OPEN_FULLMUTEX,
            nil
        ) == SQLITE_OK, let database else {
            if let database {
                sqlite3_close(database)
            }
            throw CocoaError(.fileReadCorruptFile)
        }
        defer { sqlite3_close(database) }

        let suffix = predicate.map { " WHERE \($0)" } ?? ""
        let sql = "SELECT gtin FROM products\(suffix) ORDER BY gtin LIMIT 1;"
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(database, sql, -1, &statement, nil) == SQLITE_OK,
              let statement else {
            throw CocoaError(.fileReadCorruptFile)
        }
        defer { sqlite3_finalize(statement) }

        guard sqlite3_step(statement) == SQLITE_ROW else {
            return nil
        }
        guard let text = sqlite3_column_text(statement, 0) else {
            throw CocoaError(.fileReadCorruptFile)
        }
        return String(cString: text)
    }
}
