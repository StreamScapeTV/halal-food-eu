import SwiftUI

struct ProductResultView: View {
    let product: ProductRecord
    let submissionCoordinator: ProductEvidenceSubmissionCoordinator

    private var text: ProductResultText { ProductResultText() }
    private var warning: ProductEvidenceWarning? {
        ProductResultPresentation.primaryWarning(for: product)
    }

    var body: some View {
        if let warning {
            EvidencePriorityWarning(warning: warning, text: text)
        }

        assessmentSection
        productSection
        reasonSection
        ingredientSections
        retailerSection
        certificationSection
        reviewSection
        correctionSection
    }

    private var assessmentSection: some View {
        Section(text.string("section.assessment")) {
            HStack(alignment: .top, spacing: 12) {
                Image(systemName: ProductResultPresentation.effectiveStatus(for: product).presentationSymbol)
                    .font(.title2)
                    .foregroundStyle(ProductResultPresentation.effectiveStatus(for: product).presentationColor)
                    .accessibilityHidden(true)

                VStack(alignment: .leading, spacing: 5) {
                    Text(ProductResultPresentation.statusTitle(for: product, text: text))
                        .font(.title3.bold())

                    Text(ProductResultPresentation.assessmentExplanation(for: product, text: text))
                        .foregroundStyle(.secondary)

                    if let recordedStatus = ProductResultPresentation.recordedStatusTitle(for: product, text: text) {
                        Label(recordedStatus, systemImage: "clock.arrow.trianglehead.counterclockwise.rotate.90")
                            .font(.footnote)
                            .foregroundStyle(.secondary)

                        LabeledContent(text.string("assessment.recordedSummary")) {
                            Text(product.assessment.summary)
                                .multilineTextAlignment(.trailing)
                        }
                        .font(.footnote)
                    } else {
                        Text(product.assessment.summary)
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                }
            }
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(
                ProductResultPresentation.accessibilityAssessmentLabel(for: product, text: text)
            )
        }
    }

    private var productSection: some View {
        Section(text.string("section.product")) {
            LabeledContent(text.string("field.name"), value: product.name)
            if let brand = product.brand, !brand.isEmpty {
                LabeledContent(text.string("field.brand"), value: brand)
            }
            if let owner = product.details?.brandOwner, !owner.isEmpty {
                LabeledContent(text.string("field.brandOwner"), value: owner)
            }
            if let quantity = product.details?.quantity, !quantity.isEmpty {
                LabeledContent(text.string("field.quantity"), value: quantity)
            }
            LabeledContent(text.string("field.gtin"), value: product.barcode.rawValue)
            if let market = product.details?.market, !market.isEmpty {
                LabeledContent(text.string("field.market"), value: market)
            }
            LabeledContent(text.string("field.catalog"), value: product.catalogVersion)
        }
    }

    private var reasonSection: some View {
        Section(text.string("section.reasons")) {
            ForEach(ProductResultPresentation.orderedReasons(for: product)) { reason in
                VStack(alignment: .leading, spacing: 4) {
                    Label(reason.title, systemImage: reason.severity.presentationSymbol)
                        .font(.headline)
                        .foregroundStyle(reason.severity.presentationColor)
                    Text(reason.detail)
                    if let ingredient = reason.ingredient, !ingredient.isEmpty {
                        Text(text.format("reason.evidence", ingredient))
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
    }

    @ViewBuilder
    private var ingredientSections: some View {
        if let observation = product.observation {
            Section(text.string("section.ingredients")) {
                Text(observation.text)
                    .textSelection(.enabled)
                    .accessibilityLabel(text.string("ingredients.exactSourceText"))
                    .accessibilityValue(observation.text)

                LabeledContent(text.string("field.language"), value: observation.languageCode)
                LabeledContent(
                    text.string("field.observed"),
                    value: observation.observedAt.map(text.date) ?? text.string("value.dateUnavailable")
                )
                if let details = observation.details {
                    LabeledContent(text.string("field.retrieved"), value: text.date(details.retrievedAt))
                    LabeledContent(
                        text.string("field.verification"),
                        value: verificationTitle(details.verificationState)
                    )
                    if let allergens = details.allergensText, !allergens.isEmpty {
                        LabeledContent(text.string("field.allergens"), value: allergens)
                    }
                    if let traces = details.tracesText, !traces.isEmpty {
                        LabeledContent(text.string("field.traces"), value: traces)
                    }
                }
                LabeledContent(text.string("field.freshness"), value: freshnessTitle(observation.freshness))
            } footer: {
                Text(text.string("ingredients.sourceTextFooter"))
            }

            Section(text.string("section.source")) {
                LabeledContent(text.string("field.source"), value: observation.source.name)
                LabeledContent(text.string("field.sourceType"), value: observation.source.kind)
                LabeledContent(
                    text.string("field.attribution"),
                    value: ProductResultPresentation.sourceAttribution(observation.source, text: text)
                )
                LabeledContent(text.string("field.license"), value: observation.source.license)
                LabeledContent(text.string("field.retrieved"), value: text.date(observation.source.retrievedAt))
                sourceReference(observation.source)
            }
        } else {
            Section(text.string("section.ingredients")) {
                Label(text.string("ingredients.none"), systemImage: "questionmark.circle")
                    .foregroundStyle(.secondary)
                    .accessibilityElement(children: .combine)
            }
        }
    }

    private var retailerSection: some View {
        Section(text.string("section.retailers")) {
            let evidence = product.details?.retailerEvidence ?? []
            if evidence.isEmpty {
                Text(text.string("retailer.none"))
                    .foregroundStyle(.secondary)
            } else {
                ForEach(evidence) { item in
                    VStack(alignment: .leading, spacing: 6) {
                        Label(
                            ProductResultPresentation.retailerStatement(item, text: text),
                            systemImage: item.kind.presentationSymbol
                        )
                        .font(.headline)

                        if let scope = item.scope, !scope.isEmpty {
                            Text(text.format("retailer.scope", scope))
                                .font(.footnote)
                        }
                        Text(item.limitations)
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                        Text(
                            text.format(
                                "retailer.source",
                                item.source.name,
                                ProductResultPresentation.sourceAttribution(item.source, text: text)
                            )
                        )
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    }
                    .accessibilityElement(children: .combine)
                }
            }
        } footer: {
            Text(text.string("retailer.footer"))
        }
    }

    @ViewBuilder
    private var certificationSection: some View {
        if !product.assessment.certifications.isEmpty {
            Section(text.string("section.certification")) {
                ForEach(product.assessment.certifications) { certification in
                    VStack(alignment: .leading, spacing: 6) {
                        Label(certification.certifyingBody, systemImage: "checkmark.seal")
                            .font(.headline)
                        LabeledContent(
                            text.string("field.certificateReference"),
                            value: certification.certificateReference
                        )
                        if let scheme = certification.scheme, !scheme.isEmpty {
                            LabeledContent(text.string("field.scheme"), value: scheme)
                        }
                        LabeledContent(text.string("field.scope"), value: certification.scope)
                        if let validFrom = certification.validFrom {
                            LabeledContent(text.string("field.effective"), value: text.date(validFrom))
                        }
                        if let validUntil = certification.validUntil {
                            LabeledContent(text.string("field.expires"), value: text.date(validUntil))
                        }
                        if let checked = certification.lastCheckedAt {
                            LabeledContent(text.string("field.lastChecked"), value: text.date(checked))
                        }
                        LabeledContent(text.string("field.source"), value: certification.source.name)
                        LabeledContent(
                            text.string("field.attribution"),
                            value: ProductResultPresentation.sourceAttribution(certification.source, text: text)
                        )
                        sourceReference(certification.source)
                    }
                    .accessibilityElement(children: .combine)
                }
            } footer: {
                Text(text.string("certification.footer"))
            }
        }
    }

    private var reviewSection: some View {
        Section(text.string("section.review")) {
            if let assessedAt = product.assessment.assessedAt {
                LabeledContent(text.string("field.assessed"), value: text.date(assessedAt))
            }
            if let reviewedAt = product.assessment.reviewedAt {
                LabeledContent(text.string("field.reviewed"), value: text.date(reviewedAt))
            } else {
                LabeledContent(text.string("field.reviewed"), value: text.string("value.notReviewed"))
            }
            if let methodologyVersion = product.assessment.methodologyVersion {
                LabeledContent(text.string("field.methodology"), value: methodologyVersion)
            }
            if let count = product.assessment.approvedReviewerCount {
                LabeledContent(text.string("field.approvedReviewers"), value: String(count))
            }
            if let recheckAt = product.assessment.recheckAt {
                LabeledContent(text.string("field.recheck"), value: text.date(recheckAt))
            }
        }
    }

    private var correctionSection: some View {
        Section {
            Button {
                submissionCoordinator.startCorrection(
                    product: product,
                    issueType: .ingredientsCorrection
                )
            } label: {
                Label(
                    product.observation == nil
                        ? text.string("action.addIngredients")
                        : text.string("action.reportIngredients"),
                    systemImage: "text.page.badge.magnifyingglass"
                )
            }

            Button {
                submissionCoordinator.startCorrection(
                    product: product,
                    issueType: .identityCorrection
                )
            } label: {
                Label(text.string("action.correctProduct"), systemImage: "pencil.and.list.clipboard")
            }

            Button {
                submissionCoordinator.startCorrection(
                    product: product,
                    issueType: .statusCertificationCorrection
                )
            } label: {
                Label(text.string("action.reportCertification"), systemImage: "checkmark.seal.text.page")
            }
        } header: {
            Text(text.string("section.report"))
        } footer: {
            Text(text.string("report.footer"))
        }
    }

    @ViewBuilder
    private func sourceReference(_ source: ProductSource) -> some View {
        if let url = URL(string: source.reference),
           url.scheme?.lowercased() == "https",
           url.host != nil {
            Link(destination: url) {
                LabeledContent(text.string("field.reference")) {
                    Label(text.string("action.openSource"), systemImage: "arrow.up.right.square")
                }
            }
        } else {
            LabeledContent(text.string("field.reference"), value: source.reference)
        }
    }

    private func verificationTitle(_ state: EvidenceVerificationState) -> String {
        switch state {
        case .humanVerified: text.string("verification.human")
        case .machineAssisted: text.string("verification.machine")
        case .unverified: text.string("verification.unverified")
        }
    }

    private func freshnessTitle(_ freshness: EvidenceFreshness) -> String {
        switch freshness {
        case .current: text.string("freshness.current")
        case .refreshRecommended: text.string("freshness.refresh")
        case .stale: text.string("freshness.stale")
        case .dateUnknown: text.string("freshness.dateUnknown")
        case .changedUnreviewed: text.string("freshness.changed")
        }
    }
}

private struct EvidencePriorityWarning: View {
    let warning: ProductEvidenceWarning
    let text: ProductResultText

    var body: some View {
        Section {
            Label {
                VStack(alignment: .leading, spacing: 3) {
                    Text(text.string(warning.titleKey))
                        .font(.headline)
                    Text(text.string(warning.detailKey))
                        .font(.footnote)
                }
            } icon: {
                Image(
                    systemName: warning.severity == .blocking
                        ? "exclamationmark.octagon.fill"
                        : "clock.badge.exclamationmark"
                )
            }
            .foregroundStyle(warning.severity == .blocking ? .orange : .secondary)
            .accessibilityElement(children: .combine)
        }
    }
}

private extension HalalStatus {
    var presentationSymbol: String {
        switch self {
        case .halalCertified: "checkmark.seal.fill"
        case .halalReviewed: "checkmark.circle.fill"
        case .notHalal: "xmark.octagon.fill"
        case .questionable: "exclamationmark.triangle.fill"
        case .unknown: "questionmark.circle.fill"
        }
    }

    var presentationColor: Color {
        switch self {
        case .halalCertified, .halalReviewed: .green
        case .notHalal: .red
        case .questionable: .orange
        case .unknown: .secondary
        }
    }
}

private extension EvidenceSeverity {
    var presentationSymbol: String {
        switch self {
        case .positive: "checkmark.circle"
        case .informational: "info.circle"
        case .caution: "exclamationmark.triangle"
        case .prohibitive: "xmark.octagon"
        }
    }

    var presentationColor: Color {
        switch self {
        case .positive: .green
        case .informational: .secondary
        case .caution: .orange
        case .prohibitive: .red
        }
    }
}

private extension RetailerEvidenceKind {
    var presentationSymbol: String {
        switch self {
        case .retailerFeedListing: "building.2"
        case .retailerObservation: "storefront"
        case .communityStoreReport: "person.2"
        }
    }
}
