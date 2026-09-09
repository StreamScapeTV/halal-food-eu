import Foundation
import Observation

@MainActor
@Observable
final class ProductEvidenceSubmissionCoordinator {
    private(set) var activeViewModel: ProductEvidenceSubmissionViewModel?
    var alertMessage: String?

    private let configuration: ProductEvidenceSubmissionRuntimeConfiguration?
    private let configurationError: String?
    private let composer: any ProductEvidenceComposer

    init(
        configuration: ProductEvidenceSubmissionRuntimeConfiguration?,
        configurationError: String? = nil,
        composer: any ProductEvidenceComposer
    ) {
        self.configuration = configuration
        self.configurationError = configurationError
        self.composer = composer
    }

    func startMissingProduct(result: ProductLookupResult) {
        startMissingProduct(
            barcode: result.barcode,
            market: result.market,
            catalogVersion: result.catalogVersion
        )
    }

    func startMissingProduct(
        barcode: Barcode,
        market: CatalogMarket = .germany,
        catalogVersion: String? = nil
    ) {
        guard let configuration else {
            alertMessage = configurationError
                ?? String(localized: "Product evidence submission is unavailable in this build.")
            return
        }
        let version = catalogVersion ?? configuration.catalogVersion
        guard !version.isEmpty else {
            alertMessage = String(localized: "Product evidence submission is unavailable because the active catalog version is unknown.")
            return
        }
        activeViewModel?.cleanup()
        activeViewModel = ProductEvidenceSubmissionViewModel(
            request: .missingProduct(
                barcode: barcode,
                catalogVersion: version,
                market: market.rawValue
            ),
            configuration: configuration,
            composer: composer
        )
    }

    func startCorrection(product: ProductRecord, issueType: ProductEvidenceIssueType) {
        guard issueType != .missingProduct else { return }
        guard let configuration else {
            alertMessage = configurationError
                ?? String(localized: "Product evidence submission is unavailable in this build.")
            return
        }
        activeViewModel?.cleanup()
        activeViewModel = ProductEvidenceSubmissionViewModel(
            request: .correction(product: product, issueType: issueType),
            configuration: configuration,
            composer: composer
        )
    }

    func dismissSubmission() {
        activeViewModel?.cleanup()
        activeViewModel = nil
    }
}
