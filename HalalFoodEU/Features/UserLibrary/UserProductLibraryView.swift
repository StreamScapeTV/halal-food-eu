import SwiftUI

struct UserProductLibraryView: View {
    @Bindable var viewModel: UserProductLibraryViewModel
    @Bindable var submissionCoordinator: ProductEvidenceSubmissionCoordinator
    let additiveReferenceCatalog: AdditiveReferenceCatalog?
    @State private var isClearHistoryConfirmationPresented = false

    var body: some View {
        List {
            Section {
                Toggle(
                    String(localized: "Save scan history", table: "UserLibrary"),
                    isOn: Binding(
                        get: { viewModel.historyEnabled },
                        set: { enabled in
                            Task { await viewModel.setHistoryEnabled(enabled) }
                        }
                    )
                )
                .accessibilityHint(
                    String(
                        localized: "When enabled, valid camera-scanned GTINs and scan times are saved only on this device.",
                        table: "UserLibrary"
                    )
                )
            } header: {
                Text(String(localized: "Privacy", table: "UserLibrary"))
            } footer: {
                Text(
                    String(
                        localized: "History is off by default. Camera images and recognized ingredient text are never stored in history.",
                        table: "UserLibrary"
                    )
                )
            }

            if viewModel.isLoading {
                Section {
                    HStack(spacing: 12) {
                        ProgressView()
                        Text(String(localized: "Loading saved products…", table: "UserLibrary"))
                    }
                    .accessibilityElement(children: .combine)
                }
            }

            Section(String(localized: "Favorites", table: "UserLibrary")) {
                if viewModel.favorites.isEmpty {
                    ContentUnavailableView(
                        String(localized: "No favorites", table: "UserLibrary"),
                        systemImage: "star",
                        description: Text(
                            String(
                                localized: "Open a current product result and choose Add to Favorites.",
                                table: "UserLibrary"
                            )
                        )
                    )
                } else {
                    ForEach(viewModel.favorites) { favorite in
                        NavigationLink {
                            SavedProductDetailView(
                                viewModel: viewModel.makeDetailViewModel(
                                    for: SavedProductReference(favorite: favorite)
                                ),
                                userLibraryViewModel: viewModel,
                                submissionCoordinator: submissionCoordinator,
                                additiveReferenceCatalog: additiveReferenceCatalog
                            )
                        } label: {
                            SavedProductRow(
                                barcode: favorite.barcode,
                                date: favorite.savedAt,
                                label: String(localized: "Saved", table: "UserLibrary")
                            )
                        }
                        .swipeActions {
                            Button(role: .destructive) {
                                Task { await viewModel.removeFavorite(favorite) }
                            } label: {
                                Label(
                                    String(localized: "Remove favorite", table: "UserLibrary"),
                                    systemImage: "star.slash"
                                )
                            }
                        }
                    }
                }
            }

            Section {
                if viewModel.history.isEmpty {
                    ContentUnavailableView(
                        String(localized: "No scan history", table: "UserLibrary"),
                        systemImage: "clock",
                        description: Text(
                            viewModel.historyEnabled
                                ? String(localized: "Future valid camera scans will appear here.", table: "UserLibrary")
                                : String(localized: "Enable scan history to save future camera scans on this device.", table: "UserLibrary")
                        )
                    )
                } else {
                    ForEach(viewModel.history) { entry in
                        NavigationLink {
                            SavedProductDetailView(
                                viewModel: viewModel.makeDetailViewModel(
                                    for: SavedProductReference(historyEntry: entry)
                                ),
                                userLibraryViewModel: viewModel,
                                submissionCoordinator: submissionCoordinator,
                                additiveReferenceCatalog: additiveReferenceCatalog
                            )
                        } label: {
                            SavedProductRow(
                                barcode: entry.barcode,
                                date: entry.scannedAt,
                                label: String(localized: "Scanned", table: "UserLibrary")
                            )
                        }
                        .swipeActions {
                            Button(role: .destructive) {
                                Task { await viewModel.deleteHistoryEntry(entry) }
                            } label: {
                                Label(
                                    String(localized: "Delete history entry", table: "UserLibrary"),
                                    systemImage: "trash"
                                )
                            }
                        }
                    }
                }
            } header: {
                HStack {
                    Text(String(localized: "Scan history", table: "UserLibrary"))
                    Spacer()
                    if !viewModel.history.isEmpty {
                        Button(role: .destructive) {
                            isClearHistoryConfirmationPresented = true
                        } label: {
                            Text(String(localized: "Clear", table: "UserLibrary"))
                        }
                    }
                }
            }
        }
        .navigationTitle(String(localized: "Saved products", table: "UserLibrary"))
        .task { await viewModel.load() }
        .confirmationDialog(
            String(localized: "Clear all scan history?", table: "UserLibrary"),
            isPresented: $isClearHistoryConfirmationPresented,
            titleVisibility: .visible
        ) {
            Button(String(localized: "Clear History", table: "UserLibrary"), role: .destructive) {
                Task { await viewModel.clearHistory() }
            }
            Button(String(localized: "Cancel", table: "UserLibrary"), role: .cancel) {}
        } message: {
            Text(
                String(
                    localized: "This removes saved scan entries from this device. Favorites are not affected.",
                    table: "UserLibrary"
                )
            )
        }
    }
}

private struct SavedProductRow: View {
    let barcode: Barcode
    let date: Date
    let label: String

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(barcode.rawValue)
                .font(.body.monospacedDigit())
            HStack(spacing: 4) {
                Text(label)
                Text(date.formatted(date: .abbreviated, time: .shortened))
            }
            .font(.footnote)
            .foregroundStyle(.secondary)
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel(
            String(
                format: String(localized: "Saved product accessibility format", table: "UserLibrary"),
                locale: .current,
                barcode.rawValue,
                label,
                date.formatted(date: .abbreviated, time: .shortened)
            )
        )
    }
}

struct SavedProductDetailView: View {
    @Bindable var viewModel: SavedProductDetailViewModel
    @Bindable var userLibraryViewModel: UserProductLibraryViewModel
    @Bindable var submissionCoordinator: ProductEvidenceSubmissionCoordinator
    let additiveReferenceCatalog: AdditiveReferenceCatalog?

    var body: some View {
        List {
            switch viewModel.state {
            case .idle, .loading:
                Section {
                    HStack(spacing: 12) {
                        ProgressView()
                        Text(String(localized: "Checking the current catalog…", table: "UserLibrary"))
                    }
                    .accessibilityElement(children: .combine)
                }
            case let .loaded(resolved):
                SavedProductChangeSection(resolved: resolved)
                if let product = resolved.currentProduct {
                    Section {
                        Button {
                            Task { await userLibraryViewModel.toggleFavorite(product) }
                        } label: {
                            Label(
                                userLibraryViewModel.isFavorite(product.barcode)
                                    ? String(localized: "Remove from Favorites", table: "UserLibrary")
                                    : String(localized: "Add to Favorites", table: "UserLibrary"),
                                systemImage: userLibraryViewModel.isFavorite(product.barcode) ? "star.fill" : "star"
                            )
                        }
                    }
                    ProductResultView(product: product, submissionCoordinator: submissionCoordinator)
                    AdditiveReferenceSection(product: product, catalog: additiveReferenceCatalog)
                } else {
                    Section {
                        ContentUnavailableView(
                            String(localized: "Product is not in the current catalog", table: "UserLibrary"),
                            systemImage: "questionmark.folder",
                            description: Text(
                                String(
                                    localized: "The saved GTIN is retained locally, but no current product record is available.",
                                    table: "UserLibrary"
                                )
                            )
                        )
                    }
                }
            case let .failed(message):
                Section {
                    ContentUnavailableView(
                        String(localized: "Saved product could not be checked", table: "UserLibrary"),
                        systemImage: "exclamationmark.triangle",
                        description: Text(message)
                    )
                    Button(String(localized: "Retry", table: "UserLibrary")) {
                        viewModel.load()
                    }
                }
            }
        }
        .navigationTitle(viewModel.reference.barcode.rawValue)
        .navigationBarTitleDisplayMode(.inline)
        .task { viewModel.load() }
        .onDisappear { viewModel.cancel() }
    }
}

private struct SavedProductChangeSection: View {
    let resolved: ResolvedSavedProduct

    var body: some View {
        Section(String(localized: "Saved record check", table: "UserLibrary")) {
            Label(message, systemImage: systemImage)
                .accessibilityLabel(message)

            if resolved.catalogVersionChanged {
                Text(
                    String(
                        localized: "The bundled catalog has changed since this item was saved. The product-level comparison above determines whether this exact record changed.",
                        table: "UserLibrary"
                    )
                )
                .font(.footnote)
                .foregroundStyle(.secondary)
            }
        }
    }

    private var message: String {
        switch resolved.changeState {
        case .unchanged:
            String(localized: "This product record matches the version you previously viewed.", table: "UserLibrary")
        case .changed:
            String(localized: "This product record has changed since you previously viewed it. Review the current evidence below.", table: "UserLibrary")
        case .noLongerPresent:
            String(localized: "This product was previously available but is not present in the current catalog.", table: "UserLibrary")
        case .nowAvailable:
            String(localized: "This GTIN was previously not found and now has a current catalog record.", table: "UserLibrary")
        }
    }

    private var systemImage: String {
        switch resolved.changeState {
        case .unchanged:
            "checkmark.circle"
        case .changed:
            "arrow.triangle.2.circlepath"
        case .noLongerPresent:
            "questionmark.folder"
        case .nowAvailable:
            "sparkles"
        }
    }
}
