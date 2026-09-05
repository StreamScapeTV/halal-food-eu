import Foundation
import SwiftUI

struct HomeView: View {
    @Bindable var viewModel: ScannerViewModel
    @Bindable var productSearchViewModel: ProductSearchViewModel
    @Bindable var userProductLibraryViewModel: UserProductLibraryViewModel
    @Bindable var ingredientOCRViewModel: IngredientOCRViewModel
    @Bindable var submissionCoordinator: ProductEvidenceSubmissionCoordinator
    let additiveReferenceCatalog: AdditiveReferenceCatalog?
    @State private var isIngredientOCRPresented = false

    init(
        viewModel: ScannerViewModel,
        productSearchViewModel: ProductSearchViewModel,
        userProductLibraryViewModel: UserProductLibraryViewModel,
        ingredientOCRViewModel: IngredientOCRViewModel,
        submissionCoordinator: ProductEvidenceSubmissionCoordinator,
        additiveReferenceCatalog: AdditiveReferenceCatalog? = nil
    ) {
        self.viewModel = viewModel
        self.productSearchViewModel = productSearchViewModel
        self.userProductLibraryViewModel = userProductLibraryViewModel
        self.ingredientOCRViewModel = ingredientOCRViewModel
        self.submissionCoordinator = submissionCoordinator
        self.additiveReferenceCatalog = additiveReferenceCatalog
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Button {
                        viewModel.isScannerPresented = true
                    } label: {
                        Label(String(localized: "Scan a product", table: "AppShell"), systemImage: "barcode.viewfinder")
                            .frame(maxWidth: .infinity, alignment: .center)
                    }
                    .accessibilityHint(String(localized: "Opens the camera barcode scanner.", table: "AppShell"))

                    NavigationLink {
                        ProductSearchView(
                            viewModel: productSearchViewModel,
                            onSelect: viewModel.lookup
                        )
                    } label: {
                        Label(
                            String(localized: "Search products", table: "ProductSearch"),
                            systemImage: "magnifyingglass"
                        )
                    }
                    .accessibilityHint(
                        String(
                            localized: "Searches the bundled catalog by product name, brand, or barcode.",
                            table: "ProductSearch"
                        )
                    )

                    Button {
                        ingredientOCRViewModel.reset()
                        isIngredientOCRPresented = true
                    } label: {
                        Label(
                            String(localized: "Scan ingredients", table: "IngredientOCR"),
                            systemImage: "text.viewfinder"
                        )
                        .frame(maxWidth: .infinity, alignment: .center)
                    }
                    .accessibilityHint(
                        String(localized: "Opens an on-device ingredient text scanner.", table: "IngredientOCR")
                    )

                    TextField(String(localized: "EAN, UPC, or GTIN", table: "AppShell"), text: $viewModel.manualBarcode)
                        .keyboardType(.numberPad)
                        .textContentType(.none)
                        .autocorrectionDisabled()
                        .accessibilityLabel(String(localized: "Barcode number", table: "AppShell"))

                    Button(String(localized: "Look up barcode", table: "AppShell")) {
                        viewModel.submitManualBarcode()
                    }
                    .disabled(viewModel.manualBarcode.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                } header: {
                    Text(String(localized: "Check a packaged food", table: "AppShell"))
                } footer: {
                    Text(String(localized: "Barcode lookup uses the catalog bundled with the app. Ingredient OCR also runs on device; neither requires a network connection.", table: "IngredientOCR"))
                }

                LookupStateContent(
                    viewModel: viewModel,
                    userProductLibraryViewModel: userProductLibraryViewModel,
                    submissionCoordinator: submissionCoordinator,
                    additiveReferenceCatalog: additiveReferenceCatalog
                )

                Section(String(localized: "Synthetic demonstration data", table: "AppShell")) {
                    Button(String(localized: "Reviewed-halal oat drink — 0200000000004", table: "AppShell")) {
                        viewModel.tryDemoBarcode("0200000000004")
                    }
                    Button(String(localized: "Not-halal gelatine sweets — 0200000000011", table: "AppShell")) {
                        viewModel.tryDemoBarcode("0200000000011")
                    }
                    Button(String(localized: "Questionable dessert — 0200000000028", table: "AppShell")) {
                        viewModel.tryDemoBarcode("0200000000028")
                    }
                }

                Section {
                    Label(String(localized: "Evidence, not a fatwa", table: "AppShell"), systemImage: "info.circle")
                        .font(.headline)
                    Text(String(localized: "Always check current packaging, the manufacturer or certifier, and a trusted qualified scholar for consequential decisions. Formulations and supply chains change.", table: "AppShell"))
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Halal Food EU")
            .task { await userProductLibraryViewModel.load() }
            .sheet(isPresented: $viewModel.isScannerPresented) {
                ScannerSheet(onScan: viewModel.acceptScan)
            }
            .sheet(isPresented: $isIngredientOCRPresented) {
                IngredientOCRView(viewModel: ingredientOCRViewModel)
            }
        }
    }
}

private struct LookupStateContent: View {
    let viewModel: ScannerViewModel
    @Bindable var userProductLibraryViewModel: UserProductLibraryViewModel
    let submissionCoordinator: ProductEvidenceSubmissionCoordinator
    let additiveReferenceCatalog: AdditiveReferenceCatalog?

    var body: some View {
        switch viewModel.lookupState {
        case .idle:
            Section {
                ContentUnavailableView(
                    String(localized: "Ready to scan", table: "AppShell"),
                    systemImage: "barcode",
                    description: Text(String(localized: "Scan a barcode, search the catalog, or enter one manually.", table: "AppShell"))
                )
            }
        case .lookingUp:
            Section {
                HStack(spacing: 12) {
                    ProgressView()
                    Text(String(localized: "Looking up the bundled catalog…", table: "AppShell"))
                }
                .accessibilityElement(children: .combine)
                .accessibilityLabel(String(localized: "Looking up the offline product catalog", table: "AppShell"))
            }
        case let .found(product):
            Section {
                Button {
                    Task { await userProductLibraryViewModel.toggleFavorite(product) }
                } label: {
                    Label(
                        userProductLibraryViewModel.isFavorite(product.barcode)
                            ? String(localized: "Remove from Favorites", table: "UserLibrary")
                            : String(localized: "Add to Favorites", table: "UserLibrary"),
                        systemImage: userProductLibraryViewModel.isFavorite(product.barcode) ? "star.fill" : "star"
                    )
                }
                .accessibilityHint(
                    String(
                        localized: "Favorites stay on this device and do not enable scan history.",
                        table: "UserLibrary"
                    )
                )
            }
            ProductResultView(product: product, submissionCoordinator: submissionCoordinator)
            AdditiveReferenceSection(product: product, catalog: additiveReferenceCatalog)
        case let .notFound(barcode):
            Section {
                ContentUnavailableView(
                    String(localized: "Product not found", table: "AppShell"),
                    systemImage: "questionmark.folder",
                    description: Text(
                        String(
                            format: String(
                                localized: "GTIN %@ is not present in this catalog version. This does not mean the product is halal or not halal.",
                                table: "AppShell"
                            ),
                            locale: .current,
                            barcode.rawValue
                        )
                    )
                )
                Button {
                    submissionCoordinator.startMissingProduct(barcode: barcode)
                } label: {
                    Label("Submit product evidence", systemImage: "envelope.badge")
                }
                .accessibilityHint(String(localized: "Prepares a private local evidence package that you can review before sending or sharing.", table: "AppShell"))
            }
        case let .invalidInput(message):
            Section {
                Label(message, systemImage: "exclamationmark.triangle")
                    .foregroundStyle(.orange)
                    .accessibilityLabel(
                        String(
                            format: String(localized: "Invalid barcode. %@", table: "AppShell"),
                            locale: .current,
                            message
                        )
                    )
            }
        case let .failed(message):
            Section {
                Label(String(localized: "Catalog lookup failed", table: "AppShell"), systemImage: "xmark.octagon")
                    .font(.headline)
                    .foregroundStyle(.red)
                Text(message)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                Button(String(localized: "Retry", table: "AppShell")) { viewModel.retry() }
            }
        }
    }
}
