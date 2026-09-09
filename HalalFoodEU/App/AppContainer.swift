import Foundation

@MainActor
struct AppContainer {
    private let catalog: any ProductCatalog
    private let productSearchCatalog: any ProductSearchCatalog
    private let userProductLibraryStore: any UserProductLibraryStore
    private let catalogVersion: String
    private let catalogModuleService: (any CatalogModuleService)?
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
        catalogModuleService = nil
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
        catalogModuleService: (any CatalogModuleService)?,
        additiveReferenceCatalog: AdditiveReferenceCatalog?,
        submissionConfiguration: ProductEvidenceSubmissionRuntimeConfiguration?,
        submissionConfigurationError: String?,
        submissionComposer: any ProductEvidenceComposer
    ) {
        self.catalog = catalog
        self.productSearchCatalog = productSearchCatalog
        self.userProductLibraryStore = userProductLibraryStore
        self.catalogVersion = catalogVersion
        self.catalogModuleService = catalogModuleService
        self.additiveReferenceCatalog = additiveReferenceCatalog
        self.submissionConfiguration = submissionConfiguration
        self.submissionConfigurationError = submissionConfigurationError
        self.submissionComposer = submissionComposer
    }

    static func live(bundle: Bundle = .main, preferences: AppPreferences) -> AppContainer {
        let composer = SystemProductEvidenceComposer()
        let additiveReferenceCatalog = try? AdditiveReferenceCatalogLoader.load(bundle: bundle)
        let userProductLibraryStore = makeLiveUserProductLibraryStore()

        guard let databaseURL = bundle.url(forResource: "catalog", withExtension: "sqlite3") else {
            return unavailableLive(
                message: "catalog.sqlite3 is missing from the application bundle",
                submissionMessage: String(localized: "Product evidence submission is unavailable because the bundled catalog is missing."),
                userProductLibraryStore: userProductLibraryStore,
                additiveReferenceCatalog: additiveReferenceCatalog,
                composer: composer
            )
        }
        guard let manifestURL = bundle.url(forResource: "catalog-manifest", withExtension: "json") else {
            return unavailableLive(
                message: "catalog-manifest.json is missing from the application bundle",
                submissionMessage: String(localized: "Product evidence submission is unavailable because the bundled catalog manifest is missing."),
                userProductLibraryStore: userProductLibraryStore,
                additiveReferenceCatalog: additiveReferenceCatalog,
                composer: composer
            )
        }

        let submissionResult: Result<ProductEvidenceSubmissionRuntimeConfiguration, Error> = Result {
            try ProductEvidenceSubmissionConfigurationLoader.load(bundle: bundle, catalogManifestURL: manifestURL)
        }
        let catalogVersion = (try? CatalogRuntimeIdentityLoader.load(manifestURL: manifestURL).catalogVersion) ?? ""
        let bundledCatalog = SQLiteProductCatalog(databaseURL: databaseURL, manifestURL: manifestURL, expectedMarket: .germany)
        let bundledSearch = SQLiteProductSearchCatalog(databaseURL: databaseURL, manifestURL: manifestURL)
        let router = MarketCatalogRouter(
            bundledGermany: .init(catalog: bundledCatalog, searchCatalog: bundledSearch, catalogVersion: catalogVersion),
            selectedMarket: .germany
        )

        let moduleService: (any CatalogModuleService)? = makeCatalogModuleService(
            bundle: bundle,
            appVersion: (bundle.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String) ?? "0.0.0",
            router: router
        )

        return AppContainer(
            catalog: router,
            productSearchCatalog: router,
            userProductLibraryStore: userProductLibraryStore,
            catalogVersion: catalogVersion,
            catalogModuleService: moduleService,
            additiveReferenceCatalog: additiveReferenceCatalog,
            submissionConfiguration: try? submissionResult.get(),
            submissionConfigurationError: submissionResult.failure?.localizedDescription,
            submissionComposer: composer
        )
    }

    static func live(bundle: Bundle = .main) -> AppContainer {
        live(bundle: bundle, preferences: AppPreferences())
    }

    func makeScannerViewModel(
        cameraHistoryConsentToken: @escaping @MainActor @Sendable () -> UInt64? = { nil },
        onCameraScanResolved: @escaping @MainActor @Sendable (ProductLookupResult, UInt64) -> Void = { _, _ in }
    ) -> ScannerViewModel {
        ScannerViewModel(
            lookupProduct: LookupProductByBarcode(
                catalog: catalog,
                fallbackCatalogVersion: catalogVersion
            ),
            cameraHistoryConsentToken: cameraHistoryConsentToken,
            onCameraScanResolved: onCameraScanResolved
        )
    }

    func makeProductSearchViewModel() -> ProductSearchViewModel {
        ProductSearchViewModel(searchProducts: SearchProducts(catalog: productSearchCatalog))
    }

    func makeUserProductLibraryViewModel() -> UserProductLibraryViewModel {
        UserProductLibraryViewModel(
            store: userProductLibraryStore,
            resolveSavedProduct: ResolveSavedProduct(catalog: catalog, currentCatalogVersion: catalogVersion),
            currentCatalogVersion: catalogVersion
        )
    }

    func makeCatalogModuleSettingsModel(preferences: AppPreferences) -> CatalogModuleSettingsModel {
        CatalogModuleSettingsModel(
            service: catalogModuleService,
            preferences: preferences,
            bundledCatalogVersion: catalogVersion
        )
    }

    func makeIngredientOCRViewModel() -> IngredientOCRViewModel {
        IngredientOCRViewModel(recognizer: VisionIngredientTextRecognizer())
    }

    func makeAdditiveReferenceCatalog() -> AdditiveReferenceCatalog? { additiveReferenceCatalog }

    func makeSubmissionCoordinator() -> ProductEvidenceSubmissionCoordinator {
        ProductEvidenceSubmissionCoordinator(
            configuration: submissionConfiguration,
            configurationError: submissionConfigurationError,
            composer: submissionComposer
        )
    }

    private static func makeCatalogModuleService(
        bundle: Bundle,
        appVersion: String,
        router: MarketCatalogRouter
    ) -> (any CatalogModuleService)? {
        guard let trustPolicyURL = bundle.url(
            forResource: "catalog-module-trust-policy-v1",
            withExtension: "json"
        ), let applicationSupport = FileManager.default.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        ).first else { return nil }
        let root = applicationSupport
            .appendingPathComponent("HalalFoodEU", isDirectory: true)
            .appendingPathComponent("catalog-modules", isDirectory: true)
        return CatalogModuleManager(
            rootDirectory: root,
            trustPolicyURL: trustPolicyURL,
            appVersion: appVersion,
            router: router,
            transport: GitHubCatalogModuleTransport()
        )
    }

    private static func makeLiveUserProductLibraryStore() -> any UserProductLibraryStore {
        guard let applicationSupport = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first else {
            return UnavailableUserProductLibraryStore(message: "the application support directory is unavailable")
        }
        return SQLiteUserProductLibrary(
            databaseURL: applicationSupport
                .appendingPathComponent("HalalFoodEU", isDirectory: true)
                .appendingPathComponent("user-library.sqlite3", isDirectory: false)
        )
    }

    private static func unavailableLive(
        message: String,
        submissionMessage: String,
        userProductLibraryStore: any UserProductLibraryStore,
        additiveReferenceCatalog: AdditiveReferenceCatalog?,
        composer: any ProductEvidenceComposer
    ) -> AppContainer {
        AppContainer(
            catalog: UnavailableProductCatalog(message: message),
            productSearchCatalog: UnavailableProductSearchCatalog(message: message),
            userProductLibraryStore: userProductLibraryStore,
            catalogVersion: "",
            catalogModuleService: nil,
            additiveReferenceCatalog: additiveReferenceCatalog,
            submissionConfiguration: nil,
            submissionConfigurationError: submissionMessage,
            submissionComposer: composer
        )
    }
}

private actor UnavailableProductCatalog: ProductCatalog {
    let message: String
    init(message: String) { self.message = message }
    func product(for barcode: Barcode) async throws -> ProductRecord? { throw ProductCatalogError.unavailable(message) }
}

private actor UnavailableProductSearchCatalog: ProductSearchCatalog {
    let message: String
    init(message: String) { self.message = message }
    func search(query: String, limit: Int, offset: Int) async throws -> ProductSearchPage {
        throw ProductCatalogError.unavailable(message)
    }
}

private actor UnavailableUserProductLibraryStore: UserProductLibraryStore {
    let message: String
    init(message: String) { self.message = message }
    func isHistoryEnabled() async throws -> Bool { throw UserProductLibraryError.unavailable(message) }
    func setHistoryEnabled(_ enabled: Bool) async throws { throw UserProductLibraryError.unavailable(message) }
    func recordScan(market: CatalogMarket, barcode: Barcode, scannedAt: Date, catalogVersion: String, versionMarker: SavedProductVersionMarker) async throws { throw UserProductLibraryError.unavailable(message) }
    func history(limit: Int) async throws -> [ScanHistoryEntry] { throw UserProductLibraryError.unavailable(message) }
    func deleteHistoryEntry(id: Int64) async throws { throw UserProductLibraryError.unavailable(message) }
    func clearHistory() async throws { throw UserProductLibraryError.unavailable(message) }
    func favorites() async throws -> [FavoriteProduct] { throw UserProductLibraryError.unavailable(message) }
    func favorite(for market: CatalogMarket, barcode: Barcode) async throws -> FavoriteProduct? { throw UserProductLibraryError.unavailable(message) }
    func setFavorite(market: CatalogMarket, barcode: Barcode, savedAt: Date, catalogVersion: String, versionMarker: SavedProductVersionMarker, isFavorite: Bool) async throws { throw UserProductLibraryError.unavailable(message) }
}

private extension Result {
    var failure: Failure? {
        guard case let .failure(error) = self else { return nil }
        return error
    }
}
