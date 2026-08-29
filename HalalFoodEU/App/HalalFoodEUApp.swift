import SwiftUI

@main
@MainActor
struct HalalFoodEUApp: App {
    @State private var scannerViewModel: ScannerViewModel

    init() {
        let container = AppContainer.live()
        _scannerViewModel = State(initialValue: container.makeScannerViewModel())
    }

    var body: some Scene {
        WindowGroup {
            HomeView(viewModel: scannerViewModel)
        }
    }
}
