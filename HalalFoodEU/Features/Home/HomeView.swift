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
                        Label("Scan a product", systemImage: "barcode.viewfinder")
                            .frame(maxWidth: .infinity, alignment: .center)
                    }
                    .accessibilityHint("Opens the camera barcode scanner.")

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

                    NavigationLink {
                        UserProductLibraryView(
                            viewModel: userProductLibraryViewModel,
                            submissionCoordinator: submissionCoordinator,
                            additiveReferenceCatalog: additiveReferenceCatalog
                        )
                    } label: {
                        Label(
                            String(localized: "Saved products", table: "UserLibrary"),
                            systemImage: "star"
                        )
                    }
                    .accessibilityHint(
                        String(
                            localized: "Opens your local favorites and optional camera scan history.",
                            table: "UserLibrary"
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

                    TextField("EAN, UPC, or GTIN", text: $viewModel.manualBarcode)
                        .keyboardType(.numberPad)
                        .textContentType(.none)
                        .autocorrectionDisabled()
                        .accessibilityLabel("Barcode number")

                    Button("Look up barcode") {
                        viewModel.submitManualBarcode()
                    }
                    .disabled(viewModel.manualBarcode.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                } header: {
                    Text("Check a packaged food")
                } footer: {
                    Text(String(localized: "Barcode lookup uses the catalog bundled with the app. Ingredient OCR also runs on device; neither requires a network connection.", table: "IngredientOCR"))
                }

                LookupStateContent(
                    viewModel: viewModel,
                    userProductLibraryViewModel: userProductLibraryViewModel,
                    submissionCoordinator: submissionCoordinator,
                    additiveReferenceCatalog: additiveReferenceCatalog
                )

                Section("Synthetic demonstration data") {
                    Button("Reviewed-halal oat drink — 0200000000004") {
                        viewModel.tryDemoBarcode("0200000000004")
                    }
                    Button("Not-halal gelatine sweets — 0200000000011") {
                        viewModel.tryDemoBarcode("0200000000011")
                    }
                    Button("Questionable dessert — 0200000000028") {
                        viewModel.tryDemoBarcode("0200000000028")
                    }
                }

                Section {
                    Label("Evidence, not a fatwa", systemImage: "info.circle")
                        .font(.headline)
                    Text("Always check current packaging, the manufacturer or certifier, and a trusted qualified scholar for consequential decisions. Formulations and supply chains change.")
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
                "Submission unavailable",
                isPresented: Binding(
                    get: { submissionCoordinator.alertMessage != nil },
                    set: { if !$0 { submissionCoordinator.alertMessage = nil } }
                )
            ) {
                Button("OK", role: .cancel) { submissionCoordinator.alertMessage = nil }
            } message: {
                Text(submissionCoordinator.alertMessage ?? "Product evidence submission is unavailable.")
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
                    "Ready to scan",
                    systemImage: "barcode",
                    description: Text("Scan a barcode, search the catalog, or enter one manually.")
                )
            }
        case .lookingUp:
            Section {
                HStack(spacing: 12) {
                    ProgressView()
                    Text("Looking up the bundled catalog…")
                }
                .accessibilityElement(children: .combine)
                .accessibilityLabel("Looking up the offline product catalog")
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
                    "Product not found",
                    systemImage: "questionmark.folder",
                    description: Text("GTIN \(barcode.rawValue) is not present in this catalog version. This does not mean the product is halal or not halal.")
                )
                Button {
                    submissionCoordinator.startMissingProduct(barcode: barcode)
                } label: {
                    Label("Submit product evidence", systemImage: "envelope.badge")
                }
                .accessibilityHint("Prepares a private local evidence package that you can review before sending or sharing.")
            }
        case let .invalidInput(message):
            Section {
                Label(message, systemImage: "exclamationmark.triangle")
                    .foregroundStyle(.orange)
                    .accessibilityLabel("Invalid barcode. \(message)")
            }
        case let .failed(message):
            Section {
                Label("Catalog lookup failed", systemImage: "xmark.octagon")
                    .font(.headline)
                    .foregroundStyle(.red)
                Text(message)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                Button("Retry") { viewModel.retry() }
            }
        }
    }
}
