import SwiftUI

@main
@MainActor
struct HalalFoodEUApp: App {
    @State private var scannerViewModel: ScannerViewModel
    @State private var productSearchViewModel: ProductSearchViewModel
    @State private var userProductLibraryViewModel: UserProductLibraryViewModel
    @State private var ingredientOCRViewModel: IngredientOCRViewModel
    @State private var submissionCoordinator: ProductEvidenceSubmissionCoordinator
    @State private var catalogModuleSettingsModel: CatalogModuleSettingsModel
    @State private var preferences: AppPreferences
    @State private var navigationModel: AppNavigationModel
    private let additiveReferenceCatalog: AdditiveReferenceCatalog?
    private let runtimeIdentity: AppRuntimeIdentity

    init() {
        let bundle = Bundle.main
        let preferences = AppPreferences()
        let container = AppContainer.live(bundle: bundle, preferences: preferences)
        let userProductLibraryViewModel = container.makeUserProductLibraryViewModel()
        let scannerViewModel = container.makeScannerViewModel(
            cameraHistoryConsentToken: {
                userProductLibraryViewModel.cameraHistoryConsentToken()
            },
            onCameraScanResolved: { result, consentToken in
                userProductLibraryViewModel.recordCameraScan(
                    result,
                    consentToken: consentToken
                )
            }
        )
        let productSearchViewModel = container.makeProductSearchViewModel()
        let catalogModuleSettingsModel = container.makeCatalogModuleSettingsModel(preferences: preferences)
        catalogModuleSettingsModel.onMarketDidChange = { _ in
            scannerViewModel.reset()
            productSearchViewModel.reset()
        }

        _preferences = State(initialValue: preferences)
        _userProductLibraryViewModel = State(initialValue: userProductLibraryViewModel)
        _scannerViewModel = State(initialValue: scannerViewModel)
        _productSearchViewModel = State(initialValue: productSearchViewModel)
        _ingredientOCRViewModel = State(initialValue: container.makeIngredientOCRViewModel())
        _submissionCoordinator = State(initialValue: container.makeSubmissionCoordinator())
        _catalogModuleSettingsModel = State(initialValue: catalogModuleSettingsModel)
        _navigationModel = State(initialValue: AppNavigationModel())
        additiveReferenceCatalog = container.makeAdditiveReferenceCatalog()
        runtimeIdentity = AppRuntimeIdentity(bundle: bundle)
    }

    var body: some Scene {
        WindowGroup {
            AppShellView(
                scannerViewModel: scannerViewModel,
                productSearchViewModel: productSearchViewModel,
                userProductLibraryViewModel: userProductLibraryViewModel,
                ingredientOCRViewModel: ingredientOCRViewModel,
                submissionCoordinator: submissionCoordinator,
                catalogModuleSettingsModel: catalogModuleSettingsModel,
                preferences: preferences,
                navigationModel: navigationModel,
                additiveReferenceCatalog: additiveReferenceCatalog,
                runtimeIdentity: runtimeIdentity
            )
        }
    }
}
