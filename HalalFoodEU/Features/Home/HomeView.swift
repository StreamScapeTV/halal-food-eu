import SwiftUI

struct HomeView: View {
    @Bindable var viewModel: ScannerViewModel

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
                    Text("Scanning and lookup use the catalog bundled with the app and work without a network connection.")
                }

                LookupStateContent(viewModel: viewModel)

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
            .sheet(isPresented: $viewModel.isScannerPresented) {
                ScannerSheet(onScan: viewModel.acceptScan)
            }
        }
    }
}

private struct LookupStateContent: View {
    let viewModel: ScannerViewModel

    var body: some View {
        switch viewModel.lookupState {
        case .idle:
            Section {
                ContentUnavailableView(
                    "Ready to scan",
                    systemImage: "barcode",
                    description: Text("Scan a barcode or enter one manually.")
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
            ProductResultView(product: product)
        case let .notFound(barcode):
            Section {
                ContentUnavailableView(
                    "Product not found",
                    systemImage: "questionmark.folder",
                    description: Text("GTIN \(barcode.rawValue) is not present in this catalog version. This does not mean the product is halal or not halal.")
                )
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
