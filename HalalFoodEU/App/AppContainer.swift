import Foundation

@MainActor
struct AppContainer {
    private let catalog: any ProductCatalog
    private let additiveReferenceCatalog: AdditiveReferenceCatalog?
    private let submissionConfiguration: ProductEvidenceSubmissionRuntimeConfiguration?
    private let submissionConfigurationError: String?
    private let submissionComposer: any ProductEvidenceComposer

    init(
        catalog: any ProductCatalog,
        additiveReferenceCatalog: AdditiveReferenceCatalog? = nil
    ) {
        self.catalog = catalog
        self.additiveReferenceCatalog = additiveReferenceCatalog
        submissionConfiguration = nil
        submissionConfigurationError = String(localized: "Product evidence submission is unavailable in this test configuration.")
        submissionComposer = SystemProductEvidenceComposer()
    }

    private init(
        catalog: any ProductCatalog,
        additiveReferenceCatalog: AdditiveReferenceCatalog?,
        submissionConfiguration: ProductEvidenceSubmissionRuntimeConfiguration?,
        submissionConfigurationError: String?,
        submissionComposer: any ProductEvidenceComposer
    ) {
        self.catalog = catalog
        self.additiveReferenceCatalog = additiveReferenceCatalog
        self.submissionConfiguration = submissionConfiguration
        self.submissionConfigurationError = submissionConfigurationError
        self.submissionComposer = submissionComposer
    }

    static func live(bundle: Bundle = .main) -> AppContainer {
        let composer = SystemProductEvidenceComposer()
        let additiveReferenceCatalog = try? AdditiveReferenceCatalogLoader.load(bundle: bundle)

        guard let databaseURL = bundle.url(forResource: "catalog", withExtension: "sqlite3") else {
            return AppContainer(
                catalog: UnavailableProductCatalog(message: "catalog.sqlite3 is missing from the application bundle"),
                additiveReferenceCatalog: additiveReferenceCatalog,
                submissionConfiguration: nil,
                submissionConfigurationError: String(localized: "Product evidence submission is unavailable because the bundled catalog is missing."),
                submissionComposer: composer
            )
        }
        guard let manifestURL = bundle.url(forResource: "catalog-manifest", withExtension: "json") else {
            return AppContainer(
                catalog: UnavailableProductCatalog(message: "catalog-manifest.json is missing from the application bundle"),
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

        return AppContainer(
            catalog: SQLiteProductCatalog(
                databaseURL: databaseURL,
                manifestURL: manifestURL
            ),
            additiveReferenceCatalog: additiveReferenceCatalog,
            submissionConfiguration: try? submissionResult.get(),
            submissionConfigurationError: submissionResult.failure?.localizedDescription,
            submissionComposer: composer
        )
    }

    func makeScannerViewModel() -> ScannerViewModel {
        ScannerViewModel(lookupProduct: LookupProductByBarcode(catalog: catalog))
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

private extension Result {
    var failure: Failure? {
        guard case let .failure(error) = self else { return nil }
        return error
    }
}
