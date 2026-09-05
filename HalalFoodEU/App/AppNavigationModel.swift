import Observation

enum AppTab: String, CaseIterable, Sendable {
    case check
    case saved
    case settings
}

@MainActor
@Observable
final class AppNavigationModel {
    var selectedTab: AppTab

    init(selectedTab: AppTab = .check) {
        self.selectedTab = selectedTab
    }

    func showSaved() {
        selectedTab = .saved
    }
}
