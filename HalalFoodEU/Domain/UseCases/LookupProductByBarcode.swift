struct ProductLookupResult: Sendable {
    let barcode: Barcode
    let market: CatalogMarket
    let catalogVersion: String
    let product: ProductRecord?

    init(
        barcode: Barcode,
        product: ProductRecord?,
        market: CatalogMarket = .germany,
        catalogVersion: String? = nil
    ) {
        self.barcode = barcode
        self.market = market
        self.catalogVersion = catalogVersion ?? product?.catalogVersion ?? ""
        self.product = product
    }
}

struct LookupProductByBarcode: Sendable {
    private let parser: BarcodePayloadParser
    private let catalog: any ProductCatalog
    private let fallbackMarket: CatalogMarket
    private let fallbackCatalogVersion: String

    init(
        parser: BarcodePayloadParser = BarcodePayloadParser(),
        catalog: any ProductCatalog,
        fallbackMarket: CatalogMarket = .germany,
        fallbackCatalogVersion: String = ""
    ) {
        self.parser = parser
        self.catalog = catalog
        self.fallbackMarket = fallbackMarket
        self.fallbackCatalogVersion = fallbackCatalogVersion
    }

    func callAsFunction(
        _ payload: String,
        symbology: Barcode.SymbologyHint = .unknown
    ) async throws -> ProductLookupResult {
        let barcode = try parser.parse(payload, symbology: symbology)
        try Task.checkCancellation()

        if let marketCatalog = catalog as? any MarketScopedProductCatalog {
            let resolved = try await marketCatalog.resolveProduct(for: barcode)
            return ProductLookupResult(
                barcode: barcode,
                product: resolved.product,
                market: resolved.market,
                catalogVersion: resolved.catalogVersion
            )
        }

        let product = try await catalog.product(for: barcode)
        return ProductLookupResult(
            barcode: barcode,
            product: product,
            market: product?.details.flatMap { CatalogMarket(rawValue: $0.market) } ?? fallbackMarket,
            catalogVersion: product?.catalogVersion ?? fallbackCatalogVersion
        )
    }
}
