import SwiftUI

@main
@MainActor
struct HalalFoodEUApp: App {
    @State private var scannerViewModel: ScannerViewModel
    @State private var ingredientOCRViewModel: IngredientOCRViewModel
    @State private var submissionCoordinator: ProductEvidenceSubmissionCoordinator
    private let additiveReferenceCatalog: AdditiveReferenceCatalog?

    init() {
        let container = AppContainer.live()
        _scannerViewModel = State(initialValue: container.makeScannerViewModel())
        _ingredientOCRViewModel = State(initialValue: container.makeIngredientOCRViewModel())
        _submissionCoordinator = State(initialValue: container.makeSubmissionCoordinator())
        additiveReferenceCatalog = container.makeAdditiveReferenceCatalog()
    }

    var body: some Scene {
        WindowGroup {
            HomeView(
                viewModel: scannerViewModel,
                ingredientOCRViewModel: ingredientOCRViewModel,
                submissionCoordinator: submissionCoordinator,
                additiveReferenceCatalog: additiveReferenceCatalog
            )
        }
    }
}
