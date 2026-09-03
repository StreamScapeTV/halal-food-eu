import SwiftUI

struct ProductSearchView: View {
    @Bindable var viewModel: ProductSearchViewModel
    let onSelect: (Barcode) -> Void

    @Environment(\.dismiss) private var dismiss

    var body: some View {
        List {
            switch viewModel.state {
            case .idle:
                ContentUnavailableView(
                    String(localized: "Search the bundled catalog", table: "ProductSearch"),
                    systemImage: "magnifyingglass",
                    description: Text(
                        String(
                            localized: "Enter a product name, brand, EAN, UPC, or GTIN. Search stays on this device.",
                            table: "ProductSearch"
                        )
                    )
                )
            case .searching:
                HStack(spacing: 12) {
                    ProgressView()
                    Text(String(localized: "Searching the offline catalog…", table: "ProductSearch"))
                }
                .accessibilityElement(children: .combine)
            case .results:
                Section {
                    ForEach(viewModel.results) { result in
                        Button {
                            onSelect(result.barcode)
                            dismiss()
                        } label: {
                            ProductSearchResultRow(result: result)
                        }
                        .buttonStyle(.plain)
                    }

                    if viewModel.hasMore {
                        Button {
                            viewModel.loadMore()
                        } label: {
                            if viewModel.isLoadingMore {
                                HStack(spacing: 10) {
                                    ProgressView()
                                    Text(String(localized: "Loading more…", table: "ProductSearch"))
                                }
                            } else {
                                Text(String(localized: "Load more", table: "ProductSearch"))
                            }
                        }
                        .disabled(viewModel.isLoadingMore)
                    }
                } footer: {
                    Text(
                        String(
                            localized: "Text matches are suggestions only. Selecting a result performs an exact barcode lookup before any product status or evidence is shown.",
                            table: "ProductSearch"
                        )
                    )
                }
            case .empty:
                ContentUnavailableView(
                    String(localized: "No matching products", table: "ProductSearch"),
                    systemImage: "magnifyingglass",
                    description: Text(
                        String(
                            localized: "Try a different product name, brand, or barcode. A missing search result does not imply a halal status.",
                            table: "ProductSearch"
                        )
                    )
                )
            case let .failed(message):
                ContentUnavailableView(
                    String(localized: "Search unavailable", table: "ProductSearch"),
                    systemImage: "exclamationmark.triangle",
                    description: Text(message)
                )
            }
        }
        .navigationTitle(String(localized: "Search products", table: "ProductSearch"))
        .navigationBarTitleDisplayMode(.inline)
        .searchable(
            text: $viewModel.query,
            placement: .navigationBarDrawer(displayMode: .always),
            prompt: Text(String(localized: "Name, brand, or barcode", table: "ProductSearch"))
        )
        .textInputAutocapitalization(.never)
        .autocorrectionDisabled()
        .onChange(of: viewModel.query) { _, _ in
            viewModel.queryDidChange()
        }
        .onSubmit(of: .search) {
            viewModel.submit()
        }
        .onDisappear {
            if viewModel.query.isEmpty {
                viewModel.reset()
            }
        }
    }
}

private struct ProductSearchResultRow: View {
    let result: ProductSearchResult

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: result.matchKind == .barcodeExact ? "barcode" : "shippingbox")
                .font(.title3)
                .foregroundStyle(.secondary)
                .accessibilityHidden(true)

            VStack(alignment: .leading, spacing: 4) {
                Text(result.name)
                    .font(.headline)
                    .foregroundStyle(.primary)
                if let brand = result.brand {
                    Text(brand)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
                HStack(spacing: 8) {
                    Text(result.barcode.rawValue)
                        .font(.caption.monospacedDigit())
                    if let quantity = result.quantity {
                        Text(quantity)
                            .font(.caption)
                    }
                }
                .foregroundStyle(.secondary)
            }
            Spacer(minLength: 0)
            Image(systemName: "chevron.right")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.tertiary)
                .accessibilityHidden(true)
        }
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
        .accessibilityLabel(accessibilityLabel)
        .accessibilityHint(
            String(
                localized: "Opens this exact barcode in the bundled catalog.",
                table: "ProductSearch"
            )
        )
    }

    private var accessibilityLabel: String {
        [result.name, result.brand, result.quantity, result.barcode.rawValue]
            .compactMap { $0 }
            .joined(separator: ", ")
    }
}
