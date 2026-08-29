import Foundation
import Testing
@testable import HalalFoodEU

@Suite("Evidence freshness")
struct FreshnessTests {
    @Test("Nine months recommends refresh and twelve months is stale")
    func boundaries() throws {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = try #require(TimeZone(secondsFromGMT: 0))
        let start = try #require(ISO8601DateFormatter().date(from: "2026-01-01T00:00:00Z"))
        let atNineMonths = try #require(calendar.date(byAdding: .month, value: 9, to: start))
        let atTwelveMonths = try #require(calendar.date(byAdding: .month, value: 12, to: start))
        let policy = EvidenceFreshnessPolicy.default

        #expect(policy.status(observedAt: start, now: atNineMonths, calendar: calendar) == .refreshRecommended)
        #expect(policy.status(observedAt: start, now: atTwelveMonths, calendar: calendar) == .stale)
    }
}
