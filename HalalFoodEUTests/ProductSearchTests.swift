import Foundation
import Testing
@testable import HalalFoodEU

@Suite("Product search use case")
struct SearchProductsTests {
    @Test("Whitespace is normalized before the repository boundary")
    func normalizesWhitespace() async throws {
        let catalog = RecordingProductSearchCatalog()
        let search = SearchProducts(catalog: catalog)

        _ = try await search("  Halal   Food\nEU  ")

        let requests = await catalog.requests
        #expect(
            requests == [
                ProductSearchRequest(
                    query: "Halal Food EU",
                    limit: SearchProducts.defaultPageSize,
                    offset: 0
                )
            ]
        )
    }

    @Test("Empty search returns without touching SQLite")
    func emptyQueryShortCircuits() async throws {
        let catalog = RecordingProductSearchCatalog()
        let search = SearchProducts(catalog: catalog)

        let page = try await search(" \n\t ")
        let requests = await catalog.requests

        #expect(page == .empty)
        #expect(requests.isEmpty)
    }

    @Test("Search bounds reject oversized query, page, and negative offset")
    func validatesBounds() async throws {
        let search = SearchProducts(catalog: RecordingProductSearchCatalog())

        do {
            _ = try await search(String(repeating: "x", count: SearchProducts.maximumQueryCharacters + 1))
            Issue.record("Expected oversized search query to fail")
        } catch ProductSearchError.queryTooLong(let maximum) {
            #expect(maximum == SearchProducts.maximumQueryCharacters)
        }

        do {
            _ = try await search("oat", limit: SearchProducts.maximumPageSize + 1)
            Issue.record("Expected oversized search page to fail")
        } catch ProductSearchError.invalidPageSize(let maximum) {
            #expect(maximum == SearchProducts.maximumPageSize)
        }

        do {
            _ = try await search("oat", offset: -1)
            Issue.record("Expected negative search offset to fail")
        } catch ProductSearchError.invalidOffset {
            // Expected.
        }
    }
}

@Suite("Product search view-model state")
@MainActor
struct ProductSearchViewModelTests {
    @Test("Successful search publishes provisional summary results")
    func successPublishesResults() async throws {
        let result = try makeSearchResult(
            barcode: "0200000000004",
            name: "Demonstration Oat Drink",
            brand: "Halal Food EU Demo"
        )
        let catalog = StaticProductSearchCatalog(
            page: ProductSearchPage(results: [result], offset: 0, hasMore: false)
        )
        let viewModel = ProductSearchViewModel(
            searchProducts: SearchProducts(catalog: catalog),
            debounceDuration: .zero
        )

        viewModel.query = "  oat   drink "
        viewModel.submit()
        try await waitUntil { viewModel.state == .results }

        #expect(viewModel.results == [result])
        #expect(!viewModel.hasMore)
        let requests = await catalog.requests
        #expect(requests.first?.query == "oat drink")
    }

    @Test("A newer query fences an obsolete slow result")
    func newerQuerySupersedesOlderResult() async throws {
        let catalog = DelayedProductSearchCatalog()
        let viewModel = ProductSearchViewModel(
            searchProducts: SearchProducts(catalog: catalog),
            debounceDuration: .zero
        )

        viewModel.query = "first"
        viewModel.submit()
        try await waitUntil { (await catalog.startedQueries).contains("first") }

        viewModel.query = "second"
        viewModel.submit()
        try await waitUntil {
            viewModel.results.first?.name == "second" && viewModel.state == .results
        }

        try await Task.sleep(for: .milliseconds(300))
        #expect(viewModel.results.map(\.name) == ["second"])
    }

    @Test("Load more appends unique results and closes the page")
    func paginationDeduplicatesAndCompletes() async throws {
        let catalog = PagingProductSearchCatalog()
        let viewModel = ProductSearchViewModel(
            searchProducts: SearchProducts(catalog: catalog),
            debounceDuration: .zero
        )

        viewModel.query = "demo"
        viewModel.submit()
        try await waitUntil { viewModel.state == .results && viewModel.hasMore }
        #expect(viewModel.results.count == 2)

        viewModel.loadMore()
        try await waitUntil { !viewModel.isLoadingMore && !viewModel.hasMore }

        let offsets = await catalog.offsets
        #expect(viewModel.results.map(\.name) == ["Alpha", "Beta", "Gamma"])
        #expect(offsets == [0, 2])
    }

    @Test("Search failure remains distinct from no results")
    func failureIsExplicit() async throws {
        let viewModel = ProductSearchViewModel(
            searchProducts: SearchProducts(catalog: FailingProductSearchCatalog()),
            debounceDuration: .zero
        )

        viewModel.query = "demo"
        viewModel.submit()
        try await waitUntil {
            if case .failed = viewModel.state { return true }
            return false
        }

        if case let .failed(message) = viewModel.state {
            #expect(message.contains("fixture search unavailable"))
        } else {
            Issue.record("Expected explicit search failure state")
        }
    }

    @Test("Reset clears state and cancels outstanding search publication")
    func resetCancelsOutstandingResult() async throws {
        let catalog = DelayedProductSearchCatalog()
        let viewModel = ProductSearchViewModel(
            searchProducts: SearchProducts(catalog: catalog),
            debounceDuration: .zero
        )

        viewModel.query = "first"
        viewModel.submit()
        try await waitUntil { (await catalog.startedQueries).contains("first") }
        viewModel.reset()

        try await Task.sleep(for: .milliseconds(300))
        #expect(viewModel.state == .idle)
        #expect(viewModel.query.isEmpty)
        #expect(viewModel.results.isEmpty)
    }

    private func waitUntil(
        attempts: Int = 150,
        condition: @MainActor () async -> Bool
    ) async throws {
        for _ in 0..<attempts {
            if await condition() { return }
            try await Task.sleep(for: .milliseconds(10))
        }
        Issue.record("Timed out waiting for product-search state")
    }
}

private struct ProductSearchRequest: Equatable, Sendable {
    let query: String
    let limit: Int
    let offset: Int
}

private actor RecordingProductSearchCatalog: ProductSearchCatalog {
    private(set) var requests: [ProductSearchRequest] = []

    func search(query: String, limit: Int, offset: Int) async throws -> ProductSearchPage {
        requests.append(ProductSearchRequest(query: query, limit: limit, offset: offset))
        return .empty
    }
}

private actor StaticProductSearchCatalog: ProductSearchCatalog {
    let page: ProductSearchPage
    private(set) var requests: [ProductSearchRequest] = []

    init(page: ProductSearchPage) {
        self.page = page
    }

    func search(query: String, limit: Int, offset: Int) async throws -> ProductSearchPage {
        requests.append(ProductSearchRequest(query: query, limit: limit, offset: offset))
        return page
    }
}

private actor DelayedProductSearchCatalog: ProductSearchCatalog {
    private(set) var startedQueries: [String] = []

    func search(query: String, limit: Int, offset: Int) async throws -> ProductSearchPage {
        startedQueries.append(query)
        if query == "first" {
            await Task.detached {
                try? await Task.sleep(for: .milliseconds(220))
            }.value
        } else {
            try await Task.sleep(for: .milliseconds(10))
        }
        let barcode = try Barcode(
            validating: query == "first" ? "0200000000004" : "0200000000028"
        )
        return ProductSearchPage(
            results: [
                ProductSearchResult(
                    barcode: barcode,
                    name: query,
                    brand: "Demo",
                    quantity: nil,
                    matchKind: .text
                )
            ],
            offset: offset,
            hasMore: false
        )
    }
}

private actor PagingProductSearchCatalog: ProductSearchCatalog {
    private(set) var offsets: [Int] = []

    func search(query: String, limit: Int, offset: Int) async throws -> ProductSearchPage {
        offsets.append(offset)
        let alpha = try makeSearchResult(barcode: "0200000000004", name: "Alpha")
        let beta = try makeSearchResult(barcode: "0200000000028", name: "Beta")
        let gamma = try makeSearchResult(barcode: "4006381333931", name: "Gamma")
        if offset == 0 {
            return ProductSearchPage(results: [alpha, beta], offset: 0, hasMore: true)
        }
        return ProductSearchPage(results: [beta, gamma], offset: offset, hasMore: false)
    }
}

private actor FailingProductSearchCatalog: ProductSearchCatalog {
    func search(query: String, limit: Int, offset: Int) async throws -> ProductSearchPage {
        throw ProductCatalogError.unavailable("fixture search unavailable")
    }
}

private func makeSearchResult(
    barcode: String,
    name: String,
    brand: String? = nil,
    quantity: String? = nil,
    matchKind: ProductSearchMatchKind = .text
) throws -> ProductSearchResult {
    ProductSearchResult(
        barcode: try Barcode(validating: barcode),
        name: name,
        brand: brand,
        quantity: quantity,
        matchKind: matchKind
    )
}
