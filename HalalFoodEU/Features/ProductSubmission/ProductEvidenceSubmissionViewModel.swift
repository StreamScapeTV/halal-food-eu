import Foundation
import Observation

@MainActor
struct ProductEvidenceDeliveryPresentation: Identifiable {
    enum Kind {
        case mail
        case share
    }

    let id = UUID()
    let kind: Kind
    let package: PreparedProductEvidenceSubmission
}

@MainActor
@Observable
final class ProductEvidenceSubmissionViewModel: Identifiable {
    let id: String
    let request: ProductEvidenceSubmissionRequest
    var draft: ProductEvidenceSubmissionDraft
    private(set) var attachments: [SanitizedProductEvidenceAttachment] = []
    private(set) var photoPreparationCount = 0
    private(set) var isPreparingPackage = false
    var deliveryPresentation: ProductEvidenceDeliveryPresentation?
    var alertMessage: String?
    var statusMessage: String?

    private let composer: any ProductEvidenceComposer
    private let sanitizer: ProductEvidenceImageSanitizer
    private let builder: PrepareProductEvidenceSubmission

    init(
        request: ProductEvidenceSubmissionRequest,
        configuration: ProductEvidenceSubmissionRuntimeConfiguration,
        composer: any ProductEvidenceComposer,
        sanitizer: ProductEvidenceImageSanitizer = ProductEvidenceImageSanitizer(),
        submissionUUID: UUID = UUID(),
        now: Date = Date()
    ) {
        id = "hfeu-submission-\(submissionUUID.uuidString.lowercased())"
        self.request = request
        draft = ProductEvidenceSubmissionDraft(request: request, observedAt: now)
        self.composer = composer
        self.sanitizer = sanitizer
        builder = PrepareProductEvidenceSubmission(
            configuration: configuration.submission,
            appVersion: configuration.appVersion,
            catalogVersion: configuration.catalogVersion
        )
    }

    var isWorking: Bool {
        photoPreparationCount > 0 || isPreparingPackage
    }

    var requiredPhotoPurposes: Set<ProductEvidencePhotoPurpose> {
        draft.issueType.requiredPhotoPurposes
    }

    func attachments(for purpose: ProductEvidencePhotoPurpose) -> [SanitizedProductEvidenceAttachment] {
        attachments.filter { $0.purpose == purpose }
    }

    func addPhotoData(_ data: Data, purpose: ProductEvidencePhotoPurpose) {
        if purpose != .ingredients {
            attachments.removeAll { $0.purpose == purpose }
        } else if attachments(for: .ingredients).count >= 3 {
            alertMessage = String(localized: "Up to three ingredient-panel photos can be attached.")
            return
        }
        if attachments.count >= PrepareProductEvidenceSubmission.maximumAttachmentCount {
            alertMessage = ProductEvidenceSubmissionError.tooManyAttachments.localizedDescription
            return
        }

        photoPreparationCount += 1
        let sanitizer = self.sanitizer
        Task { [weak self] in
            defer { self?.photoPreparationCount -= 1 }
            do {
                let prepared = try await Task.detached(priority: .userInitiated) {
                    try sanitizer.sanitize(data, purpose: purpose)
                }.value
                guard let self else { return }
                attachments.append(prepared)
                statusMessage = String(localized: "Photo prepared locally. Location and other image metadata are not copied into the attachment.")
            } catch {
                self?.alertMessage = error.localizedDescription
            }
        }
    }

    func removeAttachment(id: UUID) {
        attachments.removeAll { $0.id == id }
    }

    func prepareEmail() {
        guard composer.route(for: .email) == .mail else {
            statusMessage = String(localized: "Mail is not configured on this device. Use Share package or Copy details instead.")
            return
        }
        preparePresentation(kind: .mail)
    }

    func prepareShare() {
        guard composer.route(for: .share) == .share else {
            statusMessage = String(localized: "Sharing is not available on this device.")
            return
        }
        preparePresentation(kind: .share)
    }

    func prepareCopyText() async -> String? {
        guard let package = await preparePackage() else { return nil }
        defer { package.cleanup() }
        statusMessage = String(localized: "Submission details copied. The app cannot confirm delivery.")
        return "To: \(package.destinationEmail)\nSubject: \(package.subject)\n\n\(package.body)"
    }

    func handleMailOutcome(_ outcome: ProductEvidenceMailOutcome) {
        switch outcome {
        case .sent:
            statusMessage = String(localized: "Mail reported the submission as sent. This does not mean it has been reviewed or accepted.")
        case .cancelled:
            statusMessage = String(localized: "Email submission cancelled.")
        case .failed:
            statusMessage = String(localized: "Email submission failed. You can try again, share the package, or copy the details.")
        }
        cleanupPresentation()
    }

    func handleShareCompletion(completed: Bool) {
        statusMessage = completed
            ? String(localized: "The share sheet completed. The app cannot confirm whether another app delivered the package.")
            : String(localized: "Sharing cancelled.")
        cleanupPresentation()
    }

    func deliveryPresentationDismissed() {
        cleanupPresentation()
    }

    func cleanup() {
        cleanupPresentation()
    }

    private func preparePresentation(kind: ProductEvidenceDeliveryPresentation.Kind) {
        Task { [weak self] in
            guard let self, let package = await preparePackage() else { return }
            deliveryPresentation = ProductEvidenceDeliveryPresentation(kind: kind, package: package)
        }
    }

    private func preparePackage() async -> PreparedProductEvidenceSubmission? {
        guard !isWorking else { return nil }
        updateConsentAcceptedAt()
        isPreparingPackage = true
        defer { isPreparingPackage = false }

        let submissionID = id
        let request = self.request
        let draft = self.draft
        let attachments = self.attachments
        let builder = self.builder
        let now = Date()
        do {
            return try await Task.detached(priority: .userInitiated) {
                try builder(
                    submissionID: submissionID,
                    request: request,
                    draft: draft,
                    attachments: attachments,
                    now: now
                )
            }.value
        } catch {
            alertMessage = error.localizedDescription
            return nil
        }
    }

    private func updateConsentAcceptedAt() {
        let allAccepted = draft.ownsOrMaySubmitPhotos
            && draft.packageEvidenceOnly
            && draft.projectMayReviewAndRedact
            && draft.redistributionRequiresReview
            && draft.noGuaranteedCatalogOrHalalOutcome
        if allAccepted {
            if draft.consentAcceptedAt == nil {
                draft.consentAcceptedAt = Date()
            }
        } else {
            draft.consentAcceptedAt = nil
        }
    }

    private func cleanupPresentation() {
        deliveryPresentation?.package.cleanup()
        deliveryPresentation = nil
    }
}
