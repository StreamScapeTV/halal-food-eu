import SwiftUI

@main
@MainActor
struct HalalFoodEUApp: App {
    @State private var scannerViewModel: ScannerViewModel
    @State private var productSearchViewModel: ProductSearchViewModel
    @State private var userProductLibraryViewModel: UserProductLibraryViewModel
    @State private var ingredientOCRViewModel: IngredientOCRViewModel
    @State private var submissionCoordinator: ProductEvidenceSubmissionCoordinator
    private let additiveReferenceCatalog: AdditiveReferenceCatalog?

    init() {
        let container = AppContainer.live()
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
        additiveReferenceCatalog = container.makeAdditiveReferenceCatalog()
    }

    var body: some Scene {
        WindowGroup {
            HomeView(
                viewModel: scannerViewModel,
                productSearchViewModel: productSearchViewModel,
                userProductLibraryViewModel: userProductLibraryViewModel,
                ingredientOCRViewModel: ingredientOCRViewModel,
                submissionCoordinator: submissionCoordinator,
                additiveReferenceCatalog: additiveReferenceCatalog
            )
        }
    }
}
