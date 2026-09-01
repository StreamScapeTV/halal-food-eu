import MessageUI
import SwiftUI
import UIKit

enum ProductEvidenceDeliveryPreference: Sendable {
    case email
    case share
}

enum ProductEvidenceDeliveryRoute: Equatable, Sendable {
    case mail
    case share
    case unavailable
}

@MainActor
protocol ProductEvidenceComposer: AnyObject {
    func route(for preference: ProductEvidenceDeliveryPreference) -> ProductEvidenceDeliveryRoute
}

@MainActor
final class SystemProductEvidenceComposer: ProductEvidenceComposer {
    func route(for preference: ProductEvidenceDeliveryPreference) -> ProductEvidenceDeliveryRoute {
        switch preference {
        case .email:
            MFMailComposeViewController.canSendMail() ? .mail : .unavailable
        case .share:
            .share
        }
    }
}

enum ProductEvidenceMailOutcome: Equatable, Sendable {
    case sent
    case cancelled
    case failed
}

@MainActor
struct ProductEvidenceMailComposerView: UIViewControllerRepresentable {
    let package: PreparedProductEvidenceSubmission
    let onCompletion: @MainActor (ProductEvidenceMailOutcome) -> Void

    func makeCoordinator() -> Coordinator {
        Coordinator(onCompletion: onCompletion)
    }

    func makeUIViewController(context: Context) -> MFMailComposeViewController {
        let controller = MFMailComposeViewController()
        controller.mailComposeDelegate = context.coordinator
        controller.setToRecipients([package.destinationEmail])
        controller.setSubject(package.subject)
        controller.setMessageBody(package.body, isHTML: false)
        for attachment in package.mailAttachments {
            controller.addAttachmentData(
                attachment.data,
                mimeType: attachment.mimeType,
                fileName: attachment.fileName
            )
        }
        return controller
    }

    func updateUIViewController(_ controller: MFMailComposeViewController, context: Context) {}

    @MainActor
    final class Coordinator: NSObject, MFMailComposeViewControllerDelegate {
        let onCompletion: @MainActor (ProductEvidenceMailOutcome) -> Void

        init(onCompletion: @escaping @MainActor (ProductEvidenceMailOutcome) -> Void) {
            self.onCompletion = onCompletion
        }

        func mailComposeController(
            _ controller: MFMailComposeViewController,
            didFinishWith result: MFMailComposeResult,
            error: Error?
        ) {
            let outcome: ProductEvidenceMailOutcome
            if error != nil {
                outcome = .failed
            } else {
                switch result {
                case .sent:
                    outcome = .sent
                case .cancelled, .saved:
                    outcome = .cancelled
                case .failed:
                    outcome = .failed
                @unknown default:
                    outcome = .failed
                }
            }
            onCompletion(outcome)
            controller.dismiss(animated: true)
        }
    }
}

@MainActor
struct ProductEvidenceShareSheetView: UIViewControllerRepresentable {
    let package: PreparedProductEvidenceSubmission
    let onCompletion: @MainActor (Bool) -> Void

    func makeCoordinator() -> Coordinator {
        Coordinator(onCompletion: onCompletion)
    }

    func makeUIViewController(context: Context) -> UIActivityViewController {
        let items: [Any] = [package.body] + package.shareItems
        let controller = UIActivityViewController(activityItems: items, applicationActivities: nil)
        controller.completionWithItemsHandler = { _, completed, _, _ in
            Task { @MainActor in
                context.coordinator.onCompletion(completed)
            }
        }
        return controller
    }

    func updateUIViewController(_ controller: UIActivityViewController, context: Context) {}

    @MainActor
    final class Coordinator: NSObject {
        let onCompletion: @MainActor (Bool) -> Void

        init(onCompletion: @escaping @MainActor (Bool) -> Void) {
            self.onCompletion = onCompletion
        }
    }
}
