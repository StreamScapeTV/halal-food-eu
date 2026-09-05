import SwiftUI

struct AppShellView: View {
    @Bindable var scannerViewModel: ScannerViewModel
    @Bindable var productSearchViewModel: ProductSearchViewModel
    @Bindable var userProductLibraryViewModel: UserProductLibraryViewModel
    @Bindable var ingredientOCRViewModel: IngredientOCRViewModel
    @Bindable var submissionCoordinator: ProductEvidenceSubmissionCoordinator
    @Bindable var preferences: AppPreferences
    @Bindable var navigationModel: AppNavigationModel
    let additiveReferenceCatalog: AdditiveReferenceCatalog?
    let runtimeIdentity: AppRuntimeIdentity

    var body: some View {
        TabView(selection: $navigationModel.selectedTab) {
            HomeView(
                viewModel: scannerViewModel,
                productSearchViewModel: productSearchViewModel,
                userProductLibraryViewModel: userProductLibraryViewModel,
                ingredientOCRViewModel: ingredientOCRViewModel,
                submissionCoordinator: submissionCoordinator,
                additiveReferenceCatalog: additiveReferenceCatalog
            )
            .tabItem {
                Label(
                    String(localized: "Check", table: "AppShell"),
                    systemImage: "checkmark.circle"
                )
            }
            .tag(AppTab.check)

            NavigationStack {
                UserProductLibraryView(
                    viewModel: userProductLibraryViewModel,
                    submissionCoordinator: submissionCoordinator,
                    additiveReferenceCatalog: additiveReferenceCatalog
                )
            }
            .tabItem {
                Label(
                    String(localized: "Saved", table: "AppShell"),
                    systemImage: "star"
                )
            }
            .tag(AppTab.saved)

            NavigationStack {
                SettingsView(
                    preferences: preferences,
                    identity: runtimeIdentity,
                    onOpenSaved: navigationModel.showSaved
                )
            }
            .tabItem {
                Label(
                    String(localized: "Settings", table: "AppShell"),
                    systemImage: "gearshape"
                )
            }
            .tag(AppTab.settings)
        }
        .preferredColorScheme(preferences.appearance.colorScheme)
        .sheet(
            isPresented: Binding(
                get: { submissionCoordinator.activeViewModel != nil },
                set: { isPresented in
                    if !isPresented { submissionCoordinator.dismissSubmission() }
                }
            ),
            onDismiss: submissionCoordinator.dismissSubmission
        ) {
            if let submissionViewModel = submissionCoordinator.activeViewModel {
                ProductEvidenceSubmissionView(viewModel: submissionViewModel)
            }
        }
        .alert(
            String(localized: "Submission unavailable"),
            isPresented: Binding(
                get: { submissionCoordinator.alertMessage != nil },
                set: { if !$0 { submissionCoordinator.alertMessage = nil } }
            )
        ) {
            Button(String(localized: "OK"), role: .cancel) { submissionCoordinator.alertMessage = nil }
        } message: {
            Text(
                submissionCoordinator.alertMessage
                    ?? String(localized: "Product evidence submission is unavailable.")
            )
        }
        .alert(
            String(localized: "Local history unavailable", table: "UserLibrary"),
            isPresented: Binding(
                get: { userProductLibraryViewModel.errorMessage != nil },
                set: { if !$0 { userProductLibraryViewModel.errorMessage = nil } }
            )
        ) {
            Button(String(localized: "OK", table: "UserLibrary"), role: .cancel) {
                userProductLibraryViewModel.errorMessage = nil
            }
        } message: {
            Text(userProductLibraryViewModel.errorMessage ?? "")
        }
    }
}
