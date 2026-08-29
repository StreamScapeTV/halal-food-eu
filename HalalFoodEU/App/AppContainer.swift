import Foundation

@MainActor
struct AppContainer {
    private let catalog: any ProductCatalog

    init(catalog: any ProductCatalog) {
        self.catalog = catalog
    }

    static func live(bundle: Bundle = .main) -> AppContainer {
        guard let databaseURL = bundle.url(forResource: "catalog", withExtension: "sqlite3") else {
            return AppContainer(
                catalog: UnavailableProductCatalog(message: "catalog.sqlite3 is missing from the application bundle")
            )
        }
        guard let manifestURL = bundle.url(forResource: "catalog-manifest", withExtension: "json") else {
            return AppContainer(
                catalog: UnavailableProductCatalog(message: "catalog-manifest.json is missing from the application bundle")
            )
        }

        return AppContainer(
            catalog: SQLiteProductCatalog(
                databaseURL: databaseURL,
                manifestURL: manifestURL
            )
        )
    }

    func makeScannerViewModel() -> ScannerViewModel {
        ScannerViewModel(lookupProduct: LookupProductByBarcode(catalog: catalog))
    }
}

private actor UnavailableProductCatalog: ProductCatalog {
    let message: String

    init(message: String) {
        self.message = message
    }

    func product(for barcode: Barcode) async throws -> ProductRecord? {
        throw ProductCatalogError.unavailable(message)
    }
}
