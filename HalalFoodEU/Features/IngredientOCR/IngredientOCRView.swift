import SwiftUI
import UIKit

struct IngredientOCRView: View {
    @Environment(\.dismiss) private var dismiss
    @Bindable var viewModel: IngredientOCRViewModel
    @State private var isCameraPresented = false
    @State private var alertMessage: String?

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Button {
                        isCameraPresented = true
                    } label: {
                        Label(ocr("Take ingredients photo"), systemImage: "camera.viewfinder")
                    }
                    .disabled(!UIImagePickerController.isSourceTypeAvailable(.camera))
                    .accessibilityHint(ocr("Opens the camera to photograph the ingredients panel."))
                } header: {
                    Text(ocr("Ingredients scanner"))
                } footer: {
                    Text(ocr("Photograph only the ingredients panel. Text recognition happens on this iPhone and the photo is not saved or uploaded by this scanner."))
                }

                stateContent
            }
            .navigationTitle(ocr("Scan ingredients"))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(ocr("Close")) { dismiss() }
                }
            }
        }
        .sheet(isPresented: $isCameraPresented) {
            IngredientOCRCameraPicker(
                onImage: { image in
                    isCameraPresented = false
                    guard let data = image.jpegData(compressionQuality: 0.95) else {
                        alertMessage = ocr("The captured ingredients photo could not be prepared.")
                        return
                    }
                    viewModel.recognize(imageData: data)
                },
                onCancel: { isCameraPresented = false }
            )
        }
        .alert(
            ocr("Ingredient scan issue"),
            isPresented: Binding(
                get: { alertMessage != nil },
                set: { if !$0 { alertMessage = nil } }
            )
        ) {
            Button(ocr("OK"), role: .cancel) { alertMessage = nil }
        } message: {
            Text(alertMessage ?? ocr("The captured ingredients photo could not be prepared."))
        }
        .onDisappear {
            viewModel.cancelActiveRecognition()
        }
    }

    @ViewBuilder
    private var stateContent: some View {
        switch viewModel.state {
        case .idle:
            Section {
                ContentUnavailableView(
                    ocr("Ready to read ingredients"),
                    systemImage: "text.viewfinder",
                    description: Text(ocr("Take a clear, close photo of the ingredients panel. You can correct the recognized text before using it."))
                )
            }
        case .recognizing:
            Section {
                HStack(spacing: 12) {
                    ProgressView()
                    Text(ocr("Reading ingredients on device…"))
                }
                .accessibilityElement(children: .combine)
                .accessibilityLabel(ocr("Reading ingredients on this device"))
            }
        case let .recognized(result):
            Section {
                TextEditor(text: $viewModel.editableText)
                    .frame(minHeight: 180)
                    .accessibilityLabel(ocr("Recognized ingredient text"))

                Button {
                    UIPasteboard.general.string = viewModel.editableText
                } label: {
                    Label(ocr("Copy recognized text"), systemImage: "doc.on.doc")
                }
                .disabled(viewModel.editableText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            } header: {
                Text(ocr("Recognized ingredients"))
            } footer: {
                Text(ocr("OCR can make mistakes. Compare this editable text with the package. It remains unverified and cannot by itself create a halal or not-halal result."))
            }

            Section(ocr("Recognition details")) {
                LabeledContent(ocr("Vision revision"), value: result.visionRevision)
                LabeledContent(
                    ocr("Language hints"),
                    value: result.effectiveRecognitionLanguages.isEmpty
                        ? ocr("Automatic")
                        : result.effectiveRecognitionLanguages.joined(separator: ", ")
                )
                LabeledContent(
                    ocr("Average confidence"),
                    value: result.averageConfidence.formatted(.percent.precision(.fractionLength(0)))
                )
            }
        case .unreadable:
            Section {
                ContentUnavailableView(
                    ocr("No readable ingredient text"),
                    systemImage: "text.magnifyingglass",
                    description: Text(ocr("Try again with the ingredients panel flatter, closer, sharper, and evenly lit."))
                )
                retryButton
            }
        case let .failed(message):
            Section {
                Label(ocr("Ingredient text recognition failed"), systemImage: "exclamationmark.triangle")
                    .font(.headline)
                Text(message)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                retryButton
            }
        }
    }

    private var retryButton: some View {
        Button {
            isCameraPresented = true
        } label: {
            Label(ocr("Take another photo"), systemImage: "camera")
        }
        .disabled(!UIImagePickerController.isSourceTypeAvailable(.camera))
    }

    private func ocr(_ key: String.LocalizationValue) -> String {
        String(localized: key, table: "IngredientOCR")
    }
}

@MainActor
private struct IngredientOCRCameraPicker: UIViewControllerRepresentable {
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
        private let onImage: @MainActor (UIImage) -> Void
        private let onCancel: @MainActor () -> Void

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
