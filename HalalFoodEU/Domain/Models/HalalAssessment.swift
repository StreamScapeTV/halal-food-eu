import Foundation

enum HalalStatus: String, Codable, Sendable {
    case halalCertified = "halal-certified"
    case halalReviewed = "halal-reviewed"
    case notHalal = "not-halal"
    case questionable
    case unknown
}

enum EvidenceSeverity: String, Codable, Equatable, Sendable {
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
    let scheme: String?
    let lastCheckedAt: Date?

    init(
        id: Int64,
        certifyingBody: String,
        certificateReference: String,
        scope: String,
        validFrom: Date?,
        validUntil: Date?,
        source: ProductSource,
        scheme: String? = nil,
        lastCheckedAt: Date? = nil
    ) {
        self.id = id
        self.certifyingBody = certifyingBody
        self.certificateReference = certificateReference
        self.scope = scope
        self.validFrom = validFrom
        self.validUntil = validUntil
        self.source = source
        self.scheme = scheme
        self.lastCheckedAt = lastCheckedAt
    }
}

struct HalalAssessment: Codable, Equatable, Sendable {
    static let missingReviewReasonCode = "MISSING-REVIEW-EVIDENCE"

    let status: HalalStatus
    let summary: String
    let methodologyVersion: String?
    let reviewedAt: Date?
    let reasons: [AssessmentReason]
    let certifications: [CertificationEvidence]
    let assessedAt: Date?
    let recheckAt: Date?
    let approvedReviewerCount: Int?

    init(
        status: HalalStatus,
        summary: String,
        methodologyVersion: String?,
        reviewedAt: Date?,
        reasons: [AssessmentReason],
        certifications: [CertificationEvidence],
        assessedAt: Date? = nil,
        recheckAt: Date? = nil,
        approvedReviewerCount: Int? = nil
    ) {
        self.status = status
        self.summary = summary
        self.methodologyVersion = methodologyVersion
        self.reviewedAt = reviewedAt
        self.reasons = reasons
        self.certifications = certifications
        self.assessedAt = assessedAt
        self.recheckAt = recheckAt
        self.approvedReviewerCount = approvedReviewerCount
    }

    static var unreviewedUnknown: HalalAssessment {
        HalalAssessment(
            status: .unknown,
            summary: "No reviewed halal assessment is available for this product.",
            methodologyVersion: nil,
            reviewedAt: nil,
            reasons: [
                AssessmentReason(
                    id: -1,
                    code: missingReviewReasonCode,
                    title: "No reviewed halal assessment",
                    detail: "This product is present in the offline catalog, but no approved halal assessment is available for the current evidence.",
                    ingredient: nil,
                    severity: .informational
                )
            ],
            certifications: [],
            assessedAt: nil,
            recheckAt: nil,
            approvedReviewerCount: nil
        )
    }
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
