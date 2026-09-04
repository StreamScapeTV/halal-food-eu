import Foundation

@MainActor
struct AppContainer {
    private let catalog: any ProductCatalog
    private let productSearchCatalog: any ProductSearchCatalog
    private let userProductLibraryStore: any UserProductLibraryStore
    private let catalogVersion: String
    private let additiveReferenceCatalog: AdditiveReferenceCatalog?
    private let submissionConfiguration: ProductEvidenceSubmissionRuntimeConfiguration?
    private let submissionConfigurationError: String?
    private let submissionComposer: any ProductEvidenceComposer

    init(
        catalog: any ProductCatalog,
        productSearchCatalog: (any ProductSearchCatalog)? = nil,
        userProductLibraryStore: (any UserProductLibraryStore)? = nil,
        catalogVersion: String = "",
        additiveReferenceCatalog: AdditiveReferenceCatalog? = nil
    ) {
        self.catalog = catalog
        self.productSearchCatalog = productSearchCatalog ?? UnavailableProductSearchCatalog(
            message: "product search is unavailable in this test configuration"
        )
        self.userProductLibraryStore = userProductLibraryStore ?? UnavailableUserProductLibraryStore(
            message: "local history and favorites are unavailable in this test configuration"
        )
        self.catalogVersion = catalogVersion
        self.additiveReferenceCatalog = additiveReferenceCatalog
        submissionConfiguration = nil
        submissionConfigurationError = String(localized: "Product evidence submission is unavailable in this test configuration.")
        submissionComposer = SystemProductEvidenceComposer()
    }

    private init(
        catalog: any ProductCatalog,
        productSearchCatalog: any ProductSearchCatalog,
        userProductLibraryStore: any UserProductLibraryStore,
        catalogVersion: String,
        additiveReferenceCatalog: AdditiveReferenceCatalog?,
        submissionConfiguration: ProductEvidenceSubmissionRuntimeConfiguration?,
        submissionConfigurationError: String?,
        submissionComposer: any ProductEvidenceComposer
    ) {
        self.catalog = catalog
        self.productSearchCatalog = productSearchCatalog
        self.userProductLibraryStore = userProductLibraryStore
        self.catalogVersion = catalogVersion
        self.additiveReferenceCatalog = additiveReferenceCatalog
        self.submissionConfiguration = submissionConfiguration
        self.submissionConfigurationError = submissionConfigurationError
        self.submissionComposer = submissionComposer
    }

    static func live(bundle: Bundle = .main) -> AppContainer {
        let composer = SystemProductEvidenceComposer()
        let additiveReferenceCatalog = try? AdditiveReferenceCatalogLoader.load(bundle: bundle)
        let userProductLibraryStore = makeLiveUserProductLibraryStore()

        guard let databaseURL = bundle.url(forResource: "catalog", withExtension: "sqlite3") else {
            let message = "catalog.sqlite3 is missing from the application bundle"
            return AppContainer(
                catalog: UnavailableProductCatalog(message: message),
                productSearchCatalog: UnavailableProductSearchCatalog(message: message),
                userProductLibraryStore: userProductLibraryStore,
                catalogVersion: "",
                additiveReferenceCatalog: additiveReferenceCatalog,
                submissionConfiguration: nil,
                submissionConfigurationError: String(localized: "Product evidence submission is unavailable because the bundled catalog is missing."),
                submissionComposer: composer
            )
        }
        guard let manifestURL = bundle.url(forResource: "catalog-manifest", withExtension: "json") else {
            let message = "catalog-manifest.json is missing from the application bundle"
            return AppContainer(
                catalog: UnavailableProductCatalog(message: message),
                productSearchCatalog: UnavailableProductSearchCatalog(message: message),
                userProductLibraryStore: userProductLibraryStore,
                catalogVersion: "",
                additiveReferenceCatalog: additiveReferenceCatalog,
                submissionConfiguration: nil,
                submissionConfigurationError: String(localized: "Product evidence submission is unavailable because the bundled catalog manifest is missing."),
                submissionComposer: composer
            )
        }

        let submissionResult: Result<ProductEvidenceSubmissionRuntimeConfiguration, Error> = Result {
            try ProductEvidenceSubmissionConfigurationLoader.load(
                bundle: bundle,
                catalogManifestURL: manifestURL
            )
        }
        let catalogVersion = (try? CatalogRuntimeIdentityLoader.load(manifestURL: manifestURL).catalogVersion) ?? ""

        return AppContainer(
            catalog: SQLiteProductCatalog(
                databaseURL: databaseURL,
                manifestURL: manifestURL
            ),
            productSearchCatalog: SQLiteProductSearchCatalog(
                databaseURL: databaseURL,
                manifestURL: manifestURL
            ),
            userProductLibraryStore: userProductLibraryStore,
            catalogVersion: catalogVersion,
            additiveReferenceCatalog: additiveReferenceCatalog,
            submissionConfiguration: try? submissionResult.get(),
            submissionConfigurationError: submissionResult.failure?.localizedDescription,
            submissionComposer: composer
        )
    }

    func makeScannerViewModel(
        onCameraScanResolved: @escaping @MainActor @Sendable (ProductLookupResult) -> Void = { _ in }
    ) -> ScannerViewModel {
        ScannerViewModel(
            lookupProduct: LookupProductByBarcode(catalog: catalog),
            onCameraScanResolved: onCameraScanResolved
        )
    }

    func makeProductSearchViewModel() -> ProductSearchViewModel {
        ProductSearchViewModel(
            searchProducts: SearchProducts(catalog: productSearchCatalog)
        )
    }

    func makeUserProductLibraryViewModel() -> UserProductLibraryViewModel {
        UserProductLibraryViewModel(
            store: userProductLibraryStore,
            resolveSavedProduct: ResolveSavedProduct(
                catalog: catalog,
                currentCatalogVersion: catalogVersion
            ),
            currentCatalogVersion: catalogVersion
        )
    }

    func makeIngredientOCRViewModel() -> IngredientOCRViewModel {
        IngredientOCRViewModel(recognizer: VisionIngredientTextRecognizer())
    }

    func makeAdditiveReferenceCatalog() -> AdditiveReferenceCatalog? {
        additiveReferenceCatalog
    }

    func makeSubmissionCoordinator() -> ProductEvidenceSubmissionCoordinator {
        ProductEvidenceSubmissionCoordinator(
            configuration: submissionConfiguration,
            configurationError: submissionConfigurationError,
            composer: submissionComposer
        )
    }

    private static func makeLiveUserProductLibraryStore() -> any UserProductLibraryStore {
        guard let applicationSupport = FileManager.default.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        ).first else {
            return UnavailableUserProductLibraryStore(
                message: "the application support directory is unavailable"
            )
        }

        let databaseURL = applicationSupport
            .appendingPathComponent("HalalFoodEU", isDirectory: true)
            .appendingPathComponent("user-library.sqlite3", isDirectory: false)
        return SQLiteUserProductLibrary(databaseURL: databaseURL)
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

private actor UnavailableProductSearchCatalog: ProductSearchCatalog {
    let message: String

    init(message: String) {
        self.message = message
    }

    func search(query: String, limit: Int, offset: Int) async throws -> ProductSearchPage {
        throw ProductCatalogError.unavailable(message)
    }
}

private actor UnavailableUserProductLibraryStore: UserProductLibraryStore {
    let message: String

    init(message: String) {
        self.message = message
    }

    func isHistoryEnabled() async throws -> Bool { throw UserProductLibraryError.unavailable(message) }
    func setHistoryEnabled(_ enabled: Bool) async throws { throw UserProductLibraryError.unavailable(message) }
    func recordScan(
        barcode: Barcode,
        scannedAt: Date,
        catalogVersion: String,
        versionMarker: SavedProductVersionMarker
    ) async throws { throw UserProductLibraryError.unavailable(message) }
    func history(limit: Int) async throws -> [ScanHistoryEntry] { throw UserProductLibraryError.unavailable(message) }
    func deleteHistoryEntry(id: Int64) async throws { throw UserProductLibraryError.unavailable(message) }
    func clearHistory() async throws { throw UserProductLibraryError.unavailable(message) }
    func favorites() async throws -> [FavoriteProduct] { throw UserProductLibraryError.unavailable(message) }
    func favorite(for barcode: Barcode) async throws -> FavoriteProduct? { throw UserProductLibraryError.unavailable(message) }
    func setFavorite(
        barcode: Barcode,
        savedAt: Date,
        catalogVersion: String,
        versionMarker: SavedProductVersionMarker,
        isFavorite: Bool
    ) async throws { throw UserProductLibraryError.unavailable(message) }
}

private extension Result {
    var failure: Failure? {
        guard case let .failure(error) = self else { return nil }
        return error
    }
}
