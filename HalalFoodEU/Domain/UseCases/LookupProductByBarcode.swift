struct ProductLookupResult: Sendable {
    let barcode: Barcode
    let product: ProductRecord?
}

struct LookupProductByBarcode: Sendable {
    private let parser: BarcodePayloadParser
    private let catalog: any ProductCatalog

    init(
        parser: BarcodePayloadParser = BarcodePayloadParser(),
        catalog: any ProductCatalog
    ) {
        self.parser = parser
        self.catalog = catalog
    }

    func callAsFunction(
        _ payload: String,
        symbology: Barcode.SymbologyHint = .unknown
    ) async throws -> ProductLookupResult {
        let barcode = try parser.parse(payload, symbology: symbology)
        try Task.checkCancellation()
        let product = try await catalog.product(for: barcode)
        return ProductLookupResult(barcode: barcode, product: product)
    }
}
