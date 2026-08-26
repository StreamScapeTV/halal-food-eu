import SwiftUI

struct ProductResultView: View {
    let product: ProductRecord

    private var freshness: EvidenceFreshness {
        EvidenceFreshnessPolicy.default.status(observedAt: product.observation.observedAt)
    }

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

            if freshness != .current {
                FreshnessWarning(freshness: freshness, observedAt: product.observation.observedAt)
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

        Section("Ingredients") {
            Text(product.observation.text)
                .textSelection(.enabled)
            LabeledContent("Language", value: product.observation.languageCode)
            LabeledContent(
                "Observed",
                value: product.observation.observedAt.formatted(date: .abbreviated, time: .omitted)
            )
        }

        Section("Evidence source") {
            LabeledContent("Source", value: product.observation.source.name)
            LabeledContent("Type", value: product.observation.source.kind)
            LabeledContent("Reference", value: product.observation.source.reference)
            LabeledContent("Data license", value: product.observation.source.license)
            LabeledContent(
                "Retrieved",
                value: product.observation.source.retrievedAt.formatted(date: .abbreviated, time: .omitted)
            )
            LabeledContent(
                "Reviewed",
                value: product.assessment.reviewedAt.formatted(date: .abbreviated, time: .omitted)
            )
            LabeledContent("Methodology", value: product.assessment.methodologyVersion)
        }
    }
}

private struct FreshnessWarning: View {
    let freshness: EvidenceFreshness
    let observedAt: Date

    var body: some View {
        Label {
            VStack(alignment: .leading, spacing: 2) {
                Text(freshness == .stale ? "Ingredient evidence is stale" : "Ingredient refresh recommended")
                    .font(.headline)
                Text("This formulation was recorded on \(observedAt.formatted(date: .long, time: .omitted)). Check the current package before relying on the result.")
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
