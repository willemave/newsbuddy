import XCTest
@testable import newsly

final class BriefingTimelineStampTests: XCTestCase {
    private var calendar: Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(secondsFromGMT: 0)!
        return calendar
    }

    func testTodayStampIncludesLocalizedExactTime() {
        let stamp = BriefingTimelineStamp.make(
            for: date("2026-06-06T17:30:00Z"),
            now: date("2026-06-06T20:00:00Z"),
            calendar: calendar,
            locale: Locale(identifier: "en_US"),
            timeZone: TimeZone(secondsFromGMT: 0)!
        )

        XCTAssertEqual(stamp, BriefingTimelineStamp(day: "TODAY", time: "5:30\u{202F}PM"))
    }

    func testOlderStampUsesStandardDayLabelAndExactTime() {
        let stamp = BriefingTimelineStamp.make(
            for: date("2026-06-04T18:45:00Z"),
            now: date("2026-06-06T20:00:00Z"),
            calendar: calendar,
            locale: Locale(identifier: "en_US"),
            timeZone: TimeZone(secondsFromGMT: 0)!
        )

        XCTAssertEqual(stamp, BriefingTimelineStamp(day: "JUN 4", time: "6:45\u{202F}PM"))
    }

    func testSeparatorPolicyOmitsSegmentsLessThanFourHoursFromAnchor() {
        let indices = BriefingTimelineSeparatorPolicy.separatorIndices(
            for: [
                date("2026-06-06T12:00:00Z"),
                date("2026-06-06T08:00:01Z"),
            ]
        )

        XCTAssertEqual(indices, [])
    }

    func testSeparatorPolicyIncludesSegmentExactlyFourHoursFromAnchor() {
        let indices = BriefingTimelineSeparatorPolicy.separatorIndices(
            for: [
                date("2026-06-06T12:00:00Z"),
                date("2026-06-06T08:00:00Z"),
            ]
        )

        XCTAssertEqual(indices, [1])
    }

    func testSeparatorPolicyUsesCumulativeDistanceFromAnchor() {
        let indices = BriefingTimelineSeparatorPolicy.separatorIndices(
            for: [
                date("2026-06-06T12:00:00Z"),
                date("2026-06-06T11:00:00Z"),
                date("2026-06-06T10:00:00Z"),
                date("2026-06-06T09:00:00Z"),
                date("2026-06-06T08:00:00Z"),
            ]
        )

        XCTAssertEqual(indices, [4])
    }

    func testSeparatorPolicyAdvancesGreedyAnchor() {
        let indices = BriefingTimelineSeparatorPolicy.separatorIndices(
            for: [
                date("2026-06-06T12:00:00Z"),
                date("2026-06-06T08:00:00Z"),
                date("2026-06-06T07:00:00Z"),
                date("2026-06-06T04:00:00Z"),
            ]
        )

        XCTAssertEqual(indices, [1, 3])
    }

    func testSeparatorPolicyDoesNotSpecialCaseMidnight() {
        let indices = BriefingTimelineSeparatorPolicy.separatorIndices(
            for: [
                date("2026-06-07T01:00:00Z"),
                date("2026-06-06T23:00:00Z"),
                date("2026-06-06T21:00:00Z"),
            ]
        )

        XCTAssertEqual(indices, [2])
    }

    func testSeparatorPolicyHandlesEmptyAndSingleSegmentInputs() {
        XCTAssertEqual(BriefingTimelineSeparatorPolicy.separatorIndices(for: []), [])
        XCTAssertEqual(
            BriefingTimelineSeparatorPolicy.separatorIndices(
                for: [date("2026-06-06T12:00:00Z")]
            ),
            []
        )
    }

    private func date(_ value: String) -> Date {
        ISO8601DateFormatter().date(from: value)!
    }
}
