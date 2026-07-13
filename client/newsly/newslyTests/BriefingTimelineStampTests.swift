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

    private func date(_ value: String) -> Date {
        ISO8601DateFormatter().date(from: value)!
    }
}
