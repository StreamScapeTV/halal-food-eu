import Testing
@testable import HalalFoodEU

@Suite("Product search barcode normalization")
struct ProductSearchBarcodeNormalizationTests {
    @Test("Exact EAN-13 search reuses canonical GTIN normalization")
    func ean13NormalizesBeforeRepositorySearch() async throws {
        let catalog = BarcodeNormalizationSearchCatalog()
        let search = SearchProducts(catalog: catalog)

        _ = try await search("0200000000004")

        let queries = await catalog.queries
        #expect(queries == ["00200000000004"])
    }

    @Test("Short numeric input remains a provisional prefix search")
    func shortNumericInputRemainsPrefix() async throws {
        let catalog = BarcodeNormalizationSearchCatalog()
        let search = SearchProducts(catalog: catalog)

        _ = try await search("0200000")

        let queries = await catalog.queries
        #expect(queries == ["0200000"])
    }

    @Test("Hyphenated exact barcode uses the same scanner normalization")
    func hyphenatedBarcodeNormalizes() async throws {
        let catalog = BarcodeNormalizationSearchCatalog()
        let search = SearchProducts(catalog: catalog)

        _ = try await search("0200-0000-0000-4")

        let queries = await catalog.queries
        #expect(queries == ["00200000000004"])
    }
}

private actor BarcodeNormalizationSearchCatalog: ProductSearchCatalog {
    private(set) var queries: [String] = []

    func search(query: String, limit: Int, offset: Int) async throws -> ProductSearchPage {
        queries.append(query)
        return .empty
    }
}
