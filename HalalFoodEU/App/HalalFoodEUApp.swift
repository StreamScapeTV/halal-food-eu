import SwiftUI

@main
@MainActor
struct HalalFoodEUApp: App {
    @State private var scannerViewModel: ScannerViewModel
    @State private var productSearchViewModel: ProductSearchViewModel
    @State private var userProductLibraryViewModel: UserProductLibraryViewModel
    @State private var ingredientOCRViewModel: IngredientOCRViewModel
    @State private var submissionCoordinator: ProductEvidenceSubmissionCoordinator
    @State private var preferences: AppPreferences
    @State private var navigationModel: AppNavigationModel
    private let additiveReferenceCatalog: AdditiveReferenceCatalog?
    private let runtimeIdentity: AppRuntimeIdentity

    init() {
        let bundle = Bundle.main
        let container = AppContainer.live(bundle: bundle)
        let userProductLibraryViewModel = container.makeUserProductLibraryViewModel()
        _userProductLibraryViewModel = State(initialValue: userProductLibraryViewModel)
        _scannerViewModel = State(
            initialValue: container.makeScannerViewModel { result in
                userProductLibraryViewModel.recordCameraScan(result)
            }
        )
        _productSearchViewModel = State(initialValue: container.makeProductSearchViewModel())
        _ingredientOCRViewModel = State(initialValue: container.makeIngredientOCRViewModel())
        _submissionCoordinator = State(initialValue: container.makeSubmissionCoordinator())
        _preferences = State(initialValue: AppPreferences())
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
                preferences: preferences,
                navigationModel: navigationModel,
                additiveReferenceCatalog: additiveReferenceCatalog,
                runtimeIdentity: runtimeIdentity
            )
        }
    }
}
