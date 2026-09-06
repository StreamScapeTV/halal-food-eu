import Foundation
import Testing
@testable import HalalFoodEU

@MainActor
@Suite("Release-readiness submission routing")
struct ReleaseReadinessSubmissionRoutingTests {
    @Test("Not-found routing starts a local missing-product draft bound to the bundled catalog version")
    func startsMissingProductFromNotFound() throws {
        let composer = ReleaseReadinessComposer()
        let configuration = ProductEvidenceSubmissionRuntimeConfiguration(
            submission: try ProductEvidenceSubmissionConfiguration(destinationEmail: "info@faruqi.dev"),
            appVersion: "0.1.0",
            catalogVersion: "2026.09.0"
        )
        let coordinator = ProductEvidenceSubmissionCoordinator(
            configuration: configuration,
            composer: composer
        )
        let barcode = try Barcode(validating: "0200000000004")

        coordinator.startMissingProduct(barcode: barcode)

        let viewModel = try #require(coordinator.activeViewModel)
        #expect(viewModel.request.barcode == barcode)
        #expect(viewModel.request.issueType == .missingProduct)
        #expect(viewModel.request.market == "DE")
        #expect(viewModel.request.catalogVersion == "2026.09.0")
        #expect(viewModel.request.currentCatalogEvidence == nil)
        #expect(coordinator.alertMessage == nil)

        coordinator.dismissSubmission()
        #expect(coordinator.activeViewModel == nil)
    }

    @Test("Missing local public configuration fails closed without creating a submission")
    func missingConfigurationFailsClosed() throws {
        let coordinator = ProductEvidenceSubmissionCoordinator(
            configuration: nil,
            configurationError: "missing local configuration",
            composer: ReleaseReadinessComposer()
        )

        coordinator.startMissingProduct(barcode: try Barcode(validating: "0200000000004"))

        #expect(coordinator.activeViewModel == nil)
        #expect(coordinator.alertMessage == "missing local configuration")
    }
}

@MainActor
private final class ReleaseReadinessComposer: ProductEvidenceComposer {
    func route(for preference: ProductEvidenceDeliveryPreference) -> ProductEvidenceDeliveryRoute {
        switch preference {
        case .email: .unavailable
        case .share: .share
        }
    }
}
