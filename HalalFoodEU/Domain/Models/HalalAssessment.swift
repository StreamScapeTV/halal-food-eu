import Foundation

enum HalalStatus: String, Codable, CaseIterable, Sendable {
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

struct AssessmentReason: Identifiable, Hashable, Codable, Sendable {
    let id: Int64
    let code: String
    let title: String
    let detail: String
    let ingredient: String?
    let severity: EvidenceSeverity
}

struct HalalAssessment: Hashable, Codable, Sendable {
    let status: HalalStatus
    let summary: String
    let methodologyVersion: String
    let reviewedAt: Date
    let reasons: [AssessmentReason]
}

enum EvidenceFreshness: Equatable, Sendable {
    case current
    case refreshRecommended
    case stale
}

struct EvidenceFreshnessPolicy: Sendable {
    let refreshRecommendedAfterMonths: Int
    let staleAfterMonths: Int

    static let `default` = EvidenceFreshnessPolicy(
        refreshRecommendedAfterMonths: 9,
        staleAfterMonths: 12
    )

    func status(observedAt: Date, now: Date = .now, calendar: Calendar = .current) -> EvidenceFreshness {
        let months = calendar.dateComponents([.month], from: observedAt, to: now).month ?? 0

        if months >= staleAfterMonths {
            return .stale
        }
        if months >= refreshRecommendedAfterMonths {
            return .refreshRecommended
        }
        return .current
    }
}
