import Foundation
import Testing
@testable import HalalFoodEU

@Suite("Bundled SQLite product search")
struct ProductSearchCatalogIntegrationTests {
    @Test("Name and brand search use the bundled index with stable ordering")
    func searchesNameAndBrand() async throws {
        let catalog = try makeCatalog()
        let search = SearchProducts(catalog: catalog)

        let namePage = try await search("  OAT   drink ")
        #expect(namePage.results.map(\.name) == ["Demonstration Oat Drink"])
        #expect(namePage.results.first?.matchKind == .text)

        let brandPage = try await search("halal food eu demo")
        #expect(
            brandPage.results.map(\.name) == [
                "Demonstration Oat Drink",
                "Demonstration Vanilla Dessert",
            ]
        )
        #expect(brandPage.results.allSatisfy { $0.matchKind == .text })
    }

    @Test("EAN display aliases preserve leading zeros and resolve to canonical GTIN-14")
    func searchesBarcodeAliases() async throws {
        let catalog = try makeCatalog()

        let ean13 = try await catalog.search(
            query: "0200000000004",
            limit: 25,
            offset: 0
        )
        #expect(ean13.results.count == 1)
        #expect(ean13.results.first?.barcode.rawValue == "00200000000004")
        #expect(ean13.results.first?.matchKind == .barcodeExact)

        let gtin14 = try await catalog.search(
            query: "00200000000004",
            limit: 25,
            offset: 0
        )
        #expect(gtin14.results.first?.barcode.rawValue == "00200000000004")
        #expect(gtin14.results.first?.matchKind == .barcodeExact)
    }

    @Test("Barcode prefixes rank deterministically without becoming exact identity")
    func searchesBarcodePrefix() async throws {
        let catalog = try makeCatalog()
        let page = try await catalog.search(
            query: "02000000000",
            limit: 25,
            offset: 0
        )

        #expect(
            page.results.map(\.barcode.rawValue) == [
                "00200000000004",
                "00200000000028",
            ]
        )
        #expect(page.results.allSatisfy { $0.matchKind == .barcodePrefix })
    }

    @Test("No-result and bounded pagination states remain distinct")
    func noResultAndPagination() async throws {
        let catalog = try makeCatalog()

        let missing = try await catalog.search(query: "999999", limit: 1, offset: 0)
        #expect(missing.results.isEmpty)
        #expect(!missing.hasMore)

        let first = try await catalog.search(query: "demo", limit: 1, offset: 0)
        #expect(first.results.count == 1)
        #expect(first.hasMore)
        #expect(first.offset == 0)

        let second = try await catalog.search(query: "demo", limit: 1, offset: 1)
        #expect(second.results.count == 1)
        #expect(!second.hasMore)
        #expect(second.offset == 1)
        #expect(first.results.first?.barcode != second.results.first?.barcode)
    }

    @Test("Missing search-index manifest binding fails closed")
    func rejectsMissingSearchIndexBinding() async throws {
        let fixture = try makeTemporaryCatalogFixture()
        defer { try? FileManager.default.removeItem(at: fixture.directory) }

        let data = try Data(contentsOf: fixture.manifest)
        var manifest = try #require(
            JSONSerialization.jsonObject(with: data) as? [String: Any],
            "catalog manifest must be a JSON object"
        )
        manifest.removeValue(forKey: "searchIndex")
        let updated = try JSONSerialization.data(
            withJSONObject: manifest,
            options: [.prettyPrinted, .sortedKeys]
        )
        try updated.write(to: fixture.manifest, options: .atomic)

        let catalog = SQLiteProductSearchCatalog(
            databaseURL: fixture.database,
            manifestURL: fixture.manifest
        )
        do {
            _ = try await catalog.search(query: "oat", limit: 25, offset: 0)
            Issue.record("Expected missing search-index metadata to fail closed")
        } catch ProductCatalogError.invalidRecord(let message) {
            #expect(message.contains("search-index"))
        } catch {
            Issue.record("Expected search-index catalog error, got \(error)")
        }
    }

    private func makeCatalog() throws -> SQLiteProductSearchCatalog {
        let bundle = Bundle(for: ProductSearchTestBundleToken.self)
        let databaseURL = try #require(
            bundle.url(forResource: "catalog", withExtension: "sqlite3"),
            "catalog.sqlite3 must be copied into the unit-test bundle"
        )
        let manifestURL = try #require(
            bundle.url(forResource: "catalog-manifest", withExtension: "json"),
            "catalog-manifest.json must be copied into the unit-test bundle"
        )
        return SQLiteProductSearchCatalog(
            databaseURL: databaseURL,
            manifestURL: manifestURL
        )
    }

    private func makeTemporaryCatalogFixture() throws -> (
        directory: URL,
        database: URL,
        manifest: URL
    ) {
        let bundle = Bundle(for: ProductSearchTestBundleToken.self)
        let sourceDatabase = try #require(
            bundle.url(forResource: "catalog", withExtension: "sqlite3")
        )
        let sourceManifest = try #require(
            bundle.url(forResource: "catalog-manifest", withExtension: "json")
        )

        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("HalalFoodEU-Search-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let database = directory.appendingPathComponent("catalog.sqlite3")
        let manifest = directory.appendingPathComponent("catalog-manifest.json")
        try FileManager.default.copyItem(at: sourceDatabase, to: database)
        try FileManager.default.copyItem(at: sourceManifest, to: manifest)
        return (directory, database, manifest)
    }
}

private final class ProductSearchTestBundleToken {}
