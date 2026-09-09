import Foundation

actor MarketCatalogRouter: MarketScopedProductCatalog, ProductSearchCatalog {
    struct Source: Sendable {
        let catalog: any ProductCatalog
        let searchCatalog: any ProductSearchCatalog
        let catalogVersion: String
    }

    private let bundledGermany: Source
    private var downloaded: [CatalogMarket: Source] = [:]
    private var selectedMarket: CatalogMarket
    private var revision: UInt64 = 0

    init(bundledGermany: Source, selectedMarket: CatalogMarket = .germany) {
        self.bundledGermany = bundledGermany
        self.selectedMarket = selectedMarket
    }

    func activeMarket() async -> CatalogMarket { selectedMarket }

    func selectMarket(_ market: CatalogMarket) throws {
        _ = try source(for: market)
        if selectedMarket != market {
            selectedMarket = market
            revision &+= 1
        }
    }

    func registerDownloadedModule(
        market: CatalogMarket,
        catalog: any ProductCatalog,
        searchCatalog: any ProductSearchCatalog,
        catalogVersion: String
    ) {
        downloaded[market] = Source(
            catalog: catalog,
            searchCatalog: searchCatalog,
            catalogVersion: catalogVersion
        )
        revision &+= 1
    }

    func removeDownloadedModule(for market: CatalogMarket) {
        downloaded[market] = nil
        revision &+= 1
    }

    func catalogVersion(for market: CatalogMarket) async throws -> String {
        try source(for: market).catalogVersion
    }

    func product(for barcode: Barcode) async throws -> ProductRecord? {
        try await resolveProduct(for: barcode).product
    }

    func resolveProduct(for barcode: Barcode) async throws -> CatalogProductResolution {
        let requestRevision = revision
        let market = selectedMarket
        let source = try source(for: market)
        let product = try await source.catalog.product(for: barcode)
        try Task.checkCancellation()
        guard revision == requestRevision, selectedMarket == market else {
            throw CancellationError()
        }
        return CatalogProductResolution(
            market: market,
            catalogVersion: source.catalogVersion,
            product: product
        )
    }

    func product(for barcode: Barcode, market: CatalogMarket) async throws -> ProductRecord? {
        let requestRevision = revision
        let source = try source(for: market)
        let product = try await source.catalog.product(for: barcode)
        try Task.checkCancellation()
        guard revision == requestRevision else { throw CancellationError() }
        return product
    }

    func search(query: String, limit: Int, offset: Int) async throws -> ProductSearchPage {
        let requestRevision = revision
        let market = selectedMarket
        let source = try source(for: market)
        let page = try await source.searchCatalog.search(query: query, limit: limit, offset: offset)
        try Task.checkCancellation()
        guard revision == requestRevision, selectedMarket == market else {
            throw CancellationError()
        }
        return page
    }

    private func source(for market: CatalogMarket) throws -> Source {
        if let source = downloaded[market] { return source }
        if market == .germany { return bundledGermany }
        throw ProductCatalogError.unavailable(
            CatalogModuleError.unavailableMarket(market.rawValue).localizedDescription
        )
    }
}
