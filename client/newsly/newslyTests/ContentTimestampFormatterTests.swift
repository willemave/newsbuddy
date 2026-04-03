//
//  ContentTimestampFormatterTests.swift
//  newslyTests
//

import Foundation
import XCTest
@testable import newsly

final class ContentTimestampFormatterTests: XCTestCase {
    func testParseSupportsMicrosecondTimestampWithoutTimezone() {
        XCTAssertNotNil(
            ContentTimestampFormatter.parse("2026-04-02T21:47:46.871157")
        )
    }

    func testDetailMetaTextDoesNotLeakRawServerTimestamp() {
        let now = makeDate("2026-04-02T22:00:00Z")
        let rawTimestamp = "2026-04-02T21:47:46.871157"

        let rendered = ContentTimestampFormatter.detailMetaText(
            from: rawTimestamp,
            now: now
        )

        XCTAssertNotNil(rendered)
        XCTAssertNotEqual(rendered, rawTimestamp)
    }

    private func makeDate(_ rawValue: String) -> Date {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.date(from: rawValue)!
    }
}
