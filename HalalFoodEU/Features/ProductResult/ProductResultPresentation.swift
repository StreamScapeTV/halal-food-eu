import Foundation

struct ProductResultText {
    let bundle: Bundle
    let locale: Locale

    init(bundle: Bundle = .main, locale: Locale = .current) {
        self.bundle = bundle
        self.locale = locale
    }

    func string(_ key: String) -> String {
        bundle.localizedString(forKey: key, value: key, table: "ProductResult")
    }

    func format(_ key: String, _ arguments: CVarArg...) -> String {
        String(format: string(key), locale: locale, arguments: arguments)
    }

    func date(_ date: Date) -> String {
        date.formatted(
            .dateTime
                .day()
                .month(.wide)
                .year()
                .locale(locale)
        )
    }
}

struct ProductEvidenceWarning: Equatable, Sendable {
    enum Severity: Int, Equatable, Sendable {
        case advisory
        case blocking
    }

    let severity: Severity
    let titleKey: String
    let detailKey: String
}

enum ProductResultPresentation {
    static func primaryWarning(for product: ProductRecord) -> ProductEvidenceWarning? {
        if !(product.details?.conflictFlags ?? []).isEmpty {
            return ProductEvidenceWarning(
                severity: .blocking,
                titleKey: "warning.conflict.title",
                detailKey: "warning.conflict.detail"
            )
        }
        guard let observation = product.observation else {
            return ProductEvidenceWarning(
                severity: .blocking,
                titleKey: "warning.missing.title",
                detailKey: "warning.missing.detail"
            )
        }
        switch observation.freshness {
        case .changedUnreviewed:
            return ProductEvidenceWarning(
                severity: .blocking,
                titleKey: "warning.changed.title",
                detailKey: "warning.changed.detail"
            )
        case .stale:
            return ProductEvidenceWarning(
                severity: .blocking,
                titleKey: "warning.stale.title",
                detailKey: "warning.stale.detail"
            )
        case .dateUnknown:
            return ProductEvidenceWarning(
                severity: .blocking,
                titleKey: "warning.dateUnknown.title",
                detailKey: "warning.dateUnknown.detail"
            )
        case .refreshRecommended:
            return ProductEvidenceWarning(
                severity: .advisory,
                titleKey: "warning.refresh.title",
                detailKey: "warning.refresh.detail"
            )
        case .current:
            if let verification = observation.details?.verificationState,
               verification != .humanVerified {
                return ProductEvidenceWarning(
                    severity: .blocking,
                    titleKey: "warning.unverified.title",
                    detailKey: "warning.unverified.detail"
                )
            }
            return nil
        }
    }

    static func effectiveStatus(for product: ProductRecord) -> HalalStatus {
        guard (product.assessment.status == .halalCertified || product.assessment.status == .halalReviewed) else {
            return product.assessment.status
        }
        guard primaryWarning(for: product)?.severity != .blocking else {
            return .unknown
        }
        return product.assessment.status
    }

    static func statusTitle(for product: ProductRecord, text: ProductResultText) -> String {
        if effectiveStatus(for: product) == .unknown,
           (product.assessment.status == .halalCertified || product.assessment.status == .halalReviewed) {
            return text.string("assessment.needsReview")
        }
        return statusTitle(effectiveStatus(for: product), text: text)
    }

    static func recordedStatusTitle(for product: ProductRecord, text: ProductResultText) -> String? {
        guard effectiveStatus(for: product) != product.assessment.status else { return nil }
        return text.format(
            "assessment.recordedFormer",
            statusTitle(product.assessment.status, text: text)
        )
    }

    static func assessmentExplanation(for product: ProductRecord, text: ProductResultText) -> String {
        if effectiveStatus(for: product) != product.assessment.status {
            return text.string("assessment.notCurrent.detail")
        }
        switch product.assessment.status {
        case .halalCertified:
            return text.string("assessment.certified.detail")
        case .halalReviewed:
            return text.string("assessment.reviewed.detail")
        case .notHalal:
            return text.string("assessment.notHalal.detail")
        case .questionable:
            return text.string("assessment.questionable.detail")
        case .unknown:
            if product.observation == nil {
                return text.string("assessment.unknown.missingIngredients")
            }
            if product.assessment.methodologyVersion == nil {
                return text.string("assessment.unknown.pendingReview")
            }
            return text.string("assessment.unknown.detail")
        }
    }

    static func accessibilityAssessmentLabel(for product: ProductRecord, text: ProductResultText) -> String {
        var components = [
            text.format("accessibility.assessment", statusTitle(for: product, text: text)),
            assessmentExplanation(for: product, text: text),
        ]
        if let recorded = recordedStatusTitle(for: product, text: text) {
            components.append(recorded)
            components.append(text.format("accessibility.recordedSummary", product.assessment.summary))
        } else {
            components.append(product.assessment.summary)
        }
        return components.joined(separator: " ")
    }

    static func orderedReasons(for product: ProductRecord) -> [AssessmentReason] {
        guard product.assessment.status == .questionable || product.assessment.status == .notHalal else {
            return product.assessment.reasons
        }
        return product.assessment.reasons.enumerated().sorted { lhs, rhs in
            let lhsRank = reasonRank(lhs.element.severity)
            let rhsRank = reasonRank(rhs.element.severity)
            if lhsRank != rhsRank { return lhsRank < rhsRank }
            return lhs.offset < rhs.offset
        }.map(\.element)
    }

    static func retailerName(_ key: String) -> String {
        switch key.lowercased() {
        case "rewe": return "REWE"
        case "lidl": return "Lidl"
        case "aldi", "aldi-de": return "ALDI"
        case "aldi-nord": return "ALDI Nord"
        case "aldi-sued", "aldi-süd": return "ALDI Süd"
        case "edeka": return "EDEKA"
        case "globus": return "GLOBUS"
        case "penny": return "PENNY"
        default:
            return key.replacingOccurrences(of: "-", with: " ")
        }
    }

    static func retailerStatement(_ evidence: RetailerEvidence, text: ProductResultText) -> String {
        let retailer = retailerName(evidence.retailerKey)
        switch evidence.kind {
        case .retailerFeedListing:
            if let date = evidence.snapshotAt ?? evidence.observedAt {
                return text.format("retailer.feed.dated", retailer, text.date(date))
            }
            return text.format("retailer.feed.undated", retailer)
        case .retailerObservation:
            if let date = evidence.observedAt {
                return text.format("retailer.observation.dated", retailer, text.date(date))
            }
            return text.format("retailer.observation.undated", retailer)
        case .communityStoreReport:
            if let date = evidence.observedAt {
                return text.format("retailer.community.dated", retailer, text.date(date))
            }
            return text.format("retailer.community.undated", retailer)
        }
    }

    static func sourceAttribution(_ source: ProductSource, text: ProductResultText) -> String {
        if let attribution = source.attribution?.trimmingCharacters(in: .whitespacesAndNewlines),
           !attribution.isEmpty {
            return attribution
        }
        return text.format("source.licenseOnly", source.license)
    }

    static func statusTitle(_ status: HalalStatus, text: ProductResultText) -> String {
        switch status {
        case .halalCertified: return text.string("status.certified")
        case .halalReviewed: return text.string("status.reviewed")
        case .notHalal: return text.string("status.notHalal")
        case .questionable: return text.string("status.questionable")
        case .unknown: return text.string("status.unknown")
        }
    }

    private static func reasonRank(_ severity: EvidenceSeverity) -> Int {
        switch severity {
        case .prohibitive: return 0
        case .caution: return 1
        case .informational: return 2
        case .positive: return 3
        }
    }
}
