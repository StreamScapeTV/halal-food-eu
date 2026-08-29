import Foundation
import Testing
@testable import HalalFoodEU

@Suite("Bundled SQLite catalog")
struct CatalogIntegrationTests {
    @Test("Loads a reviewed product and ordered reasons from the real SQLite resource")
    func loadsKnownProduct() async throws {
        let catalog = try makeCatalog()
        let barcode = try Barcode(validating: "0200000000004")
        let product = try #require(try await catalog.product(for: barcode))

        #expect(product.name == "Demonstration Oat Drink")
        #expect(product.assessment.status == .halalReviewed)
        #expect(product.assessment.reasons.map(\.code) == [
            "DEMO-INGREDIENTS-REVIEWED",
            "NO-CERTIFICATION-CLAIM",
        ])
        #expect(product.catalogVersion == "0.1.0-demo.1")
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

    @Test("An unsupported manifest schema fails before SQLite reads")
    func rejectsUnsupportedManifestSchema() async throws {
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
            Issue.record("Unsupported manifest schemas must fail closed")
        } catch ProductCatalogError.incompatibleSchema(let expected, let actual) {
            #expect(expected == 1)
            #expect(actual == 999)
        } catch {
            Issue.record("Expected an incompatible schema error, got \(error)")
        }
    }

    @Test("Malformed source-policy identity in the manifest fails closed")
    func rejectsInvalidSourcePolicyIdentity() async throws {
        let fixture = try makeTemporaryCatalogFixture()
        defer { try? FileManager.default.removeItem(at: fixture.directory) }

        try mutateManifest(at: fixture.manifest) { manifest in
            guard var policy = manifest["sourcePolicy"] as? [String: Any] else {
                throw CocoaError(.fileReadCorruptFile)
            }
            policy["schemaVersion"] = 999
            manifest["sourcePolicy"] = policy
        }

        let catalog = SQLiteProductCatalog(
            databaseURL: fixture.database,
            manifestURL: fixture.manifest
        )
        let barcode = try Barcode(validating: "0200000000004")

        do {
            _ = try await catalog.product(for: barcode)
            Issue.record("Unsupported source-policy identity must fail closed")
        } catch ProductCatalogError.invalidRecord(let message) {
            #expect(message.contains("source-policy"))
        } catch {
            Issue.record("Expected an invalid catalog record error, got \(error)")
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
