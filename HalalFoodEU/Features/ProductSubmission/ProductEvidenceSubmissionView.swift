import PhotosUI
import SwiftUI
import UIKit

struct ProductEvidenceSubmissionView: View {
    @Environment(\.dismiss) private var dismiss
    @Bindable var viewModel: ProductEvidenceSubmissionViewModel
    @State private var cameraPurpose: ProductEvidencePhotoPurpose?

    var body: some View {
        NavigationStack {
            Form {
                Section("Product") {
                    LabeledContent("GTIN", value: viewModel.request.barcode.rawValue)
                    LabeledContent("Market", value: viewModel.request.market)
                    LabeledContent("Catalog", value: viewModel.request.catalogVersion)
                    if viewModel.request.issueType == .missingProduct {
                        LabeledContent("Issue", value: ProductEvidenceIssueType.missingProduct.localizedTitle)
                    } else {
                        Picker("Issue", selection: $viewModel.draft.issueType) {
                            ForEach(ProductEvidenceIssueType.correctionCases) { type in
                                Text(type.localizedTitle).tag(type)
                            }
                        }
                    }
                    TextField("Product name", text: $viewModel.draft.productName)
                    TextField("Brand", text: $viewModel.draft.brand)
                    TextField("Quantity", text: $viewModel.draft.quantity)
                    DatePicker(
                        "Package observed",
                        selection: $viewModel.draft.observedAt,
                        in: ...Date(),
                        displayedComponents: .date
                    )
                }

                Section {
                    TextField("Retailer", text: $viewModel.draft.retailer)
                    TextField("City", text: $viewModel.draft.city)
                    TextField("Store", text: $viewModel.draft.store)
                } header: {
                    Text("Optional retailer context")
                } footer: {
                    Text("Location is optional and typed by you. The app does not request device location permission.")
                }

                Section {
                    ForEach(ProductEvidencePhotoPurpose.allCases) { purpose in
                        ProductEvidencePhotoPurposeRow(
                            purpose: purpose,
                            isRequired: viewModel.requiredPhotoPurposes.contains(purpose),
                            viewModel: viewModel,
                            takePhoto: { cameraPurpose = purpose }
                        )
                    }
                } header: {
                    Text("Package photos")
                } footer: {
                    Text("Choose only package evidence you own or may submit. Crop or redact personal information before choosing it when needed. Every selected image is re-encoded locally; location and other image metadata are not copied into the prepared attachment.")
                }

                Section {
                    TextEditor(text: $viewModel.draft.notes)
                        .frame(minHeight: 96)
                        .accessibilityLabel("Optional notes")
                } header: {
                    Text("Notes")
                } footer: {
                    Text("Do not include credentials, receipts, payment data, faces, addresses, account details, or unrelated personal information.")
                }

                Section {
                    Toggle(
                        "I took these photos or have permission to submit them.",
                        isOn: $viewModel.draft.ownsOrMaySubmitPhotos
                    )
                    Toggle(
                        "The photos contain package evidence only and no prohibited personal information.",
                        isOn: $viewModel.draft.packageEvidenceOnly
                    )
                    Toggle(
                        "The project may store, review, crop, redact, and use this evidence for product-data verification.",
                        isOn: $viewModel.draft.projectMayReviewAndRedact
                    )
                    Toggle(
                        "Catalog redistribution remains subject to a separate rights and human review.",
                        isOn: $viewModel.draft.redistributionRequiresReview
                    )
                    Toggle(
                        "I understand submission does not guarantee inclusion or any halal result.",
                        isOn: $viewModel.draft.noGuaranteedCatalogOrHalalOutcome
                    )
                } header: {
                    Text("Consent and rights")
                } footer: {
                    Text("Nothing is uploaded silently. You review the final email or share sheet before any data leaves the device. Email transport is outside the app's end-to-end control.")
                }

                if viewModel.isWorking {
                    Section {
                        HStack(spacing: 12) {
                            ProgressView()
                            Text(viewModel.photoPreparationCount > 0 ? "Preparing photo locally…" : "Preparing submission package…")
                        }
                        .accessibilityElement(children: .combine)
                    }
                }

                Section {
                    Button {
                        viewModel.prepareEmail()
                    } label: {
                        Label("Review email", systemImage: "envelope")
                    }
                    .disabled(viewModel.isWorking)

                    Button {
                        viewModel.prepareShare()
                    } label: {
                        Label("Share submission package", systemImage: "square.and.arrow.up")
                    }
                    .disabled(viewModel.isWorking)

                    Button {
                        Task {
                            if let text = await viewModel.prepareCopyText() {
                                UIPasteboard.general.string = text
                            }
                        }
                    } label: {
                        Label("Copy submission details and address", systemImage: "doc.on.doc")
                    }
                    .disabled(viewModel.isWorking)
                } header: {
                    Text("Send or share")
                } footer: {
                    Text("The app can report only the Mail composer's sent, cancelled, or failed result. A share sheet or copied package does not prove delivery or acceptance.")
                }

                if let statusMessage = viewModel.statusMessage {
                    Section("Submission status") {
                        Text(statusMessage)
                            .accessibilityLabel("Submission status. \(statusMessage)")
                    }
                }
            }
            .navigationTitle("Submit product evidence")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Close") { dismiss() }
                }
            }
        }
        .sheet(item: $cameraPurpose) { purpose in
            ProductEvidenceCameraPicker(
                purpose: purpose,
                onImage: { image in
                    cameraPurpose = nil
                    guard let data = image.jpegData(compressionQuality: 0.95) else {
                        viewModel.alertMessage = String(localized: "The captured photo could not be prepared.")
                        return
                    }
                    viewModel.addPhotoData(data, purpose: purpose)
                },
                onCancel: { cameraPurpose = nil }
            )
        }
        .sheet(
            item: $viewModel.deliveryPresentation,
            onDismiss: viewModel.deliveryPresentationDismissed
        ) { presentation in
            switch presentation.kind {
            case .mail:
                ProductEvidenceMailComposerView(
                    package: presentation.package,
                    onCompletion: viewModel.handleMailOutcome
                )
            case .share:
                ProductEvidenceShareSheetView(
                    package: presentation.package,
                    onCompletion: viewModel.handleShareCompletion
                )
            }
        }
        .alert(
            "Submission issue",
            isPresented: Binding(
                get: { viewModel.alertMessage != nil },
                set: { if !$0 { viewModel.alertMessage = nil } }
            )
        ) {
            Button("OK", role: .cancel) { viewModel.alertMessage = nil }
        } message: {
            Text(viewModel.alertMessage ?? "The submission could not be prepared.")
        }
    }
}

private struct ProductEvidencePhotoPurposeRow: View {
    let purpose: ProductEvidencePhotoPurpose
    let isRequired: Bool
    @Bindable var viewModel: ProductEvidenceSubmissionViewModel
    let takePhoto: @MainActor () -> Void
    @State private var selectedItem: PhotosPickerItem?

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(purpose.localizedTitle)
                    .font(.headline)
                if isRequired {
                    Text("Required")
                        .font(.caption.bold())
                        .accessibilityLabel("Required photo")
                }
                Spacer()
            }

            ForEach(viewModel.attachments(for: purpose)) { attachment in
                HStack {
                    Label(
                        "\(attachment.pixelWidth) × \(attachment.pixelHeight) JPEG",
                        systemImage: "photo"
                    )
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    Spacer()
                    Button("Remove", role: .destructive) {
                        viewModel.removeAttachment(id: attachment.id)
                    }
                    .font(.footnote)
                }
            }

            HStack {
                PhotosPicker(selection: $selectedItem, matching: .images) {
                    Label("Choose photo", systemImage: "photo.on.rectangle")
                }

                Button(action: takePhoto) {
                    Label("Take photo", systemImage: "camera")
                }
                .disabled(!UIImagePickerController.isSourceTypeAvailable(.camera))
            }
            .buttonStyle(.borderless)
        }
        .onChange(of: selectedItem) { _, item in
            guard let item else { return }
            Task {
                do {
                    guard let data = try await item.loadTransferable(type: Data.self) else {
                        viewModel.alertMessage = String(localized: "The selected photo could not be loaded.")
                        selectedItem = nil
                        return
                    }
                    viewModel.addPhotoData(data, purpose: purpose)
                    selectedItem = nil
                } catch {
                    viewModel.alertMessage = error.localizedDescription
                    selectedItem = nil
                }
            }
        }
    }
}

@MainActor
private struct ProductEvidenceCameraPicker: UIViewControllerRepresentable {
    let purpose: ProductEvidencePhotoPurpose
    let onImage: @MainActor (UIImage) -> Void
    let onCancel: @MainActor () -> Void

    func makeCoordinator() -> Coordinator {
        Coordinator(onImage: onImage, onCancel: onCancel)
    }

    func makeUIViewController(context: Context) -> UIViewController {
        guard UIImagePickerController.isSourceTypeAvailable(.camera) else {
            let controller = UIViewController()
            controller.view.backgroundColor = .systemBackground
            Task { @MainActor in onCancel() }
            return controller
        }
        let picker = UIImagePickerController()
        picker.sourceType = .camera
        picker.cameraCaptureMode = .photo
        picker.allowsEditing = true
        picker.delegate = context.coordinator
        return picker
    }

    func updateUIViewController(_ controller: UIViewController, context: Context) {}

    @MainActor
    final class Coordinator: NSObject, @MainActor UINavigationControllerDelegate, @MainActor UIImagePickerControllerDelegate {
        let onImage: @MainActor (UIImage) -> Void
        let onCancel: @MainActor () -> Void

        init(
            onImage: @escaping @MainActor (UIImage) -> Void,
            onCancel: @escaping @MainActor () -> Void
        ) {
            self.onImage = onImage
            self.onCancel = onCancel
        }

        func imagePickerController(
            _ picker: UIImagePickerController,
            didFinishPickingMediaWithInfo info: [UIImagePickerController.InfoKey: Any]
        ) {
            guard let image = (info[.editedImage] ?? info[.originalImage]) as? UIImage else {
                onCancel()
                return
            }
            onImage(image)
        }

        func imagePickerControllerDidCancel(_ picker: UIImagePickerController) {
            onCancel()
        }
    }
}

private extension ProductEvidenceIssueType {
    static var correctionCases: [ProductEvidenceIssueType] {
        [.ingredientsCorrection, .identityCorrection, .statusCertificationCorrection]
    }

    var localizedTitle: String {
        switch self {
        case .missingProduct:
            String(localized: "Missing product")
        case .ingredientsCorrection:
            String(localized: "Ingredients correction")
        case .identityCorrection:
            String(localized: "Product details correction")
        case .statusCertificationCorrection:
            String(localized: "Certification or result concern")
        }
    }
}

private extension ProductEvidencePhotoPurpose {
    var localizedTitle: String {
        switch self {
        case .barcode:
            String(localized: "Barcode with visible digits")
        case .front:
            String(localized: "Full front of package")
        case .ingredients:
            String(localized: "Ingredients panel")
        case .certification:
            String(localized: "Certification mark or details")
        case .nutrition:
            String(localized: "Nutrition or variant panel")
        }
    }
}
