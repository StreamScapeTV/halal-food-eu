import SwiftUI

struct ProductResultView: View {
    let product: ProductRecord
    let submissionCoordinator: ProductEvidenceSubmissionCoordinator

    var body: some View {
        Section("Assessment") {
            HStack(alignment: .top, spacing: 12) {
                Image(systemName: product.assessment.status.presentationSymbol)
                    .font(.title2)
                    .foregroundStyle(product.assessment.status.presentationColor)
                    .accessibilityHidden(true)

                VStack(alignment: .leading, spacing: 4) {
                    Text(product.assessment.status.presentationTitle)
                        .font(.title3.bold())
                    Text(product.assessment.summary)
                        .foregroundStyle(.secondary)
                }
            }
            .accessibilityElement(children: .combine)
            .accessibilityLabel(
                "Assessment: \(product.assessment.status.presentationTitle). \(product.assessment.summary)"
            )

            if let observation = product.observation, observation.freshness != .current {
                FreshnessWarning(
                    freshness: observation.freshness,
                    observedAt: observation.observedAt
                )
            } else if product.observation == nil {
                MissingFormulationWarning()
            }
        }

        Section("Product") {
            LabeledContent("Name", value: product.name)
            if let brand = product.brand, !brand.isEmpty {
                LabeledContent("Brand", value: brand)
            }
            LabeledContent("GTIN", value: product.barcode.rawValue)
            LabeledContent("Catalog", value: product.catalogVersion)
        }

        Section("Why this result") {
            ForEach(product.assessment.reasons) { reason in
                VStack(alignment: .leading, spacing: 4) {
                    Label(reason.title, systemImage: reason.severity.presentationSymbol)
                        .font(.headline)
                        .foregroundStyle(reason.severity.presentationColor)
                    Text(reason.detail)
                    if let ingredient = reason.ingredient, !ingredient.isEmpty {
                        Text("Evidence: \(ingredient)")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                    Text(reason.code)
                        .font(.caption.monospaced())
                        .foregroundStyle(.tertiary)
                }
                .accessibilityElement(children: .combine)
            }
        }

        if let observation = product.observation {
            Section("Ingredients") {
                Text(observation.text)
                    .textSelection(.enabled)
                LabeledContent("Language", value: observation.languageCode)
                if let observedAt = observation.observedAt {
                    LabeledContent(
                        "Observed",
                        value: observedAt.formatted(date: .abbreviated, time: .omitted)
                    )
                } else {
                    LabeledContent("Observed", value: "Date unavailable")
                }
            }

            Section("Evidence source") {
                LabeledContent("Source", value: observation.source.name)
                LabeledContent("Type", value: observation.source.kind)
                LabeledContent("Reference", value: observation.source.reference)
                LabeledContent("Data license", value: observation.source.license)
                LabeledContent(
                    "Retrieved",
                    value: observation.source.retrievedAt.formatted(date: .abbreviated, time: .omitted)
                )
            }
        } else {
            Section("Ingredients") {
                Text("No reviewed ingredient formulation is available for this product in the current offline catalog.")
                    .foregroundStyle(.secondary)
            }
        }

        Section("Review") {
            if let reviewedAt = product.assessment.reviewedAt {
                LabeledContent(
                    "Reviewed",
                    value: reviewedAt.formatted(date: .abbreviated, time: .omitted)
                )
            } else {
                LabeledContent("Reviewed", value: "Not reviewed")
            }
            if let methodologyVersion = product.assessment.methodologyVersion {
                LabeledContent("Methodology", value: methodologyVersion)
            }
        }

        Section {
            Button {
                submissionCoordinator.startCorrection(
                    product: product,
                    issueType: .ingredientsCorrection
                )
            } label: {
                Label(
                    product.observation == nil ? "Add missing ingredient evidence" : "Report ingredient evidence",
                    systemImage: "text.page.badge.magnifyingglass"
                )
            }

            Button {
                submissionCoordinator.startCorrection(
                    product: product,
                    issueType: .identityCorrection
                )
            } label: {
                Label("Correct product details", systemImage: "pencil.and.list.clipboard")
            }

            Button {
                submissionCoordinator.startCorrection(
                    product: product,
                    issueType: .statusCertificationCorrection
                )
            } label: {
                Label("Report certification or result concern", systemImage: "checkmark.seal.text.page")
            }
        } header: {
            Text("Report or correct")
        } footer: {
            Text("A report stays on this device until you explicitly review an email, share the package, or copy its details. Submissions are untrusted evidence until human review.")
        }
    }
}

private struct MissingFormulationWarning: View {
    var body: some View {
        Label {
            VStack(alignment: .leading, spacing: 2) {
                Text("Ingredient evidence is unavailable")
                    .font(.headline)
                Text("The catalog keeps this product as unknown rather than inferring a halal outcome without a reviewed formulation. Check the current package before relying on the result.")
                    .font(.footnote)
            }
        } icon: {
            Image(systemName: "questionmark.circle")
        }
        .foregroundStyle(.orange)
        .accessibilityElement(children: .combine)
    }
}

private struct FreshnessWarning: View {
    let freshness: EvidenceFreshness
    let observedAt: Date?

    private var title: String {
        switch freshness {
        case .current:
            "Ingredient evidence is current"
        case .refreshRecommended:
            "Ingredient refresh recommended"
        case .stale:
            "Ingredient evidence is stale"
        case .dateUnknown:
            "Ingredient evidence date is unknown"
        case .changedUnreviewed:
            "Formulation change needs review"
        }
    }

    private var detail: String {
        switch freshness {
        case .current:
            return "The reviewed formulation is current under the catalog policy."
        case .refreshRecommended, .stale:
            if let observedAt {
                return "This formulation was recorded on \(observedAt.formatted(date: .long, time: .omitted)). Check the current package before relying on the result."
            }
            return "The formulation needs a freshness check. Check the current package before relying on the result."
        case .dateUnknown:
            return "The catalog could not establish when this formulation was observed. Check the current package before relying on the result."
        case .changedUnreviewed:
            return "A newer formulation exists without sufficient review. Treat the current result cautiously and check the package."
        }
    }

    var body: some View {
        Label {
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.headline)
                Text(detail)
                    .font(.footnote)
            }
        } icon: {
            Image(systemName: "clock.badge.exclamationmark")
        }
        .foregroundStyle(.orange)
        .accessibilityElement(children: .combine)
    }
}

private extension HalalStatus {
    var presentationTitle: String {
        switch self {
        case .halalCertified:
            "Halal certified"
        case .halalReviewed:
            "Halal — reviewed"
        case .notHalal:
            "Not halal"
        case .questionable:
            "Questionable"
        case .unknown:
            "Unknown"
        }
    }

    var presentationSymbol: String {
        switch self {
        case .halalCertified:
            "checkmark.seal.fill"
        case .halalReviewed:
            "checkmark.circle.fill"
        case .notHalal:
            "xmark.octagon.fill"
        case .questionable:
            "exclamationmark.triangle.fill"
        case .unknown:
            "questionmark.circle.fill"
        }
    }

    var presentationColor: Color {
        switch self {
        case .halalCertified, .halalReviewed:
            .green
        case .notHalal:
            .red
        case .questionable:
            .orange
        case .unknown:
            .secondary
        }
    }
}

private extension EvidenceSeverity {
    var presentationSymbol: String {
        switch self {
        case .positive:
            "checkmark.circle"
        case .informational:
            "info.circle"
        case .caution:
            "exclamationmark.triangle"
        case .prohibitive:
            "xmark.octagon"
        }
    }

    var presentationColor: Color {
        switch self {
        case .positive:
            .green
        case .informational:
            .secondary
        case .caution:
            .orange
        case .prohibitive:
            .red
        }
    }
}
