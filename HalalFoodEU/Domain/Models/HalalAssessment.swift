import Foundation

enum HalalStatus: String, Codable, Sendable {
    case halalCertified = "halal-certified"
    case halalReviewed = "halal-reviewed"
    case notHalal = "not-halal"
    case questionable
    case unknown
}

enum EvidenceSeverity: String, Codable, Sendable {
    case positive
    case informational
    case caution
    case prohibitive
}

struct AssessmentReason: Codable, Equatable, Sendable, Identifiable {
    let id: Int64
    let code: String
    let title: String
    let detail: String
    let ingredient: String?
    let severity: EvidenceSeverity
}

struct CertificationEvidence: Codable, Equatable, Sendable, Identifiable {
    let id: Int64
    let certifyingBody: String
    let certificateReference: String
    let scope: String
    let validFrom: Date?
    let validUntil: Date?
    let source: ProductSource
}

struct HalalAssessment: Codable, Equatable, Sendable {
    let status: HalalStatus
    let summary: String
    let methodologyVersion: String
    let reviewedAt: Date
    let reasons: [AssessmentReason]
    let certifications: [CertificationEvidence]
}

enum EvidenceFreshness: String, Codable, Equatable, Sendable {
    case current = "fresh"
    case refreshRecommended = "refresh-recommended"
    case stale
    case dateUnknown = "date-unknown"
    case changedUnreviewed = "changed-unreviewed"
}

struct EvidenceFreshnessPolicy: Equatable, Sendable {
    let refreshRecommendedAfterMonths: Int
    let staleAfterMonths: Int

    static let `default` = EvidenceFreshnessPolicy(
        refreshRecommendedAfterMonths: 9,
        staleAfterMonths: 12
    )

    func status(
        observedAt: Date,
        now: Date = Date(),
        calendar: Calendar = Calendar(identifier: .gregorian)
    ) -> EvidenceFreshness {
        guard let staleAt = calendar.date(
            byAdding: .month,
            value: staleAfterMonths,
            to: observedAt
        ), let refreshAt = calendar.date(
            byAdding: .month,
            value: refreshRecommendedAfterMonths,
            to: observedAt
        ) else {
            return .stale
        }

        if now >= staleAt {
            return .stale
        }
        if now >= refreshAt {
            return .refreshRecommended
        }
        return .current
    }
}
