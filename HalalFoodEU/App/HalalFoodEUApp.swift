import SwiftUI

@main
@MainActor
struct HalalFoodEUApp: App {
    @State private var scannerViewModel: ScannerViewModel
    @State private var submissionCoordinator: ProductEvidenceSubmissionCoordinator

    init() {
        let container = AppContainer.live()
        _scannerViewModel = State(initialValue: container.makeScannerViewModel())
        _submissionCoordinator = State(initialValue: container.makeSubmissionCoordinator())
    }

    var body: some Scene {
        WindowGroup {
            HomeView(
                viewModel: scannerViewModel,
                submissionCoordinator: submissionCoordinator
            )
        }
    }
}
