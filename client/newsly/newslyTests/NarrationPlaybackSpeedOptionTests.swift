import XCTest
@testable import newsly

final class NarrationPlaybackSpeedOptionTests: XCTestCase {
    func testStandardOptionsExposeExpectedRatesAndTitles() {
        let options = NarrationPlaybackSpeedOption.standardOptions

        XCTAssertEqual(options.count, 3)
        XCTAssertEqual(options[0].rate, 1.0, accuracy: 0.001)
        XCTAssertEqual(options[1].rate, 1.25, accuracy: 0.001)
        XCTAssertEqual(options[2].rate, 1.5, accuracy: 0.001)
        XCTAssertEqual(options.map(\.title), ["1x", "1.25x", "1.5x"])
    }

    func testAccessibilityActionNameMatchesTitle() {
        let option = NarrationPlaybackSpeedOption(rate: 1.25, title: "1.25x")

        XCTAssertEqual(option.accessibilityActionName, "Play at 1.25x")
    }

    func testTitleForRateFallsBackToDefaultWhenRateIsUnknown() {
        XCTAssertEqual(NarrationPlaybackSpeedOption.title(for: 1.25), "1.25x")
        XCTAssertEqual(NarrationPlaybackSpeedOption.title(for: 9.0), "1x")
    }

    func testPreferenceStorePersistsSupportedPlaybackRate() {
        let suiteName = "NarrationPlaybackPreferenceStoreTests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suiteName)!
        let store = NarrationPlaybackPreferenceStore(
            defaults: defaults,
            storageKey: "preferred_rate"
        )

        defer {
            defaults.removePersistentDomain(forName: suiteName)
        }

        XCTAssertEqual(store.preferredPlaybackRate(), 1.0, accuracy: 0.001)

        store.savePreferredPlaybackRate(1.25)

        XCTAssertEqual(store.preferredPlaybackRate(), 1.25, accuracy: 0.001)
    }

    func testPreferenceStoreFallsBackToDefaultForUnsupportedStoredRate() {
        let suiteName = "NarrationPlaybackPreferenceStoreTests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suiteName)!
        let store = NarrationPlaybackPreferenceStore(
            defaults: defaults,
            storageKey: "preferred_rate"
        )

        defer {
            defaults.removePersistentDomain(forName: suiteName)
        }

        defaults.set(9.0, forKey: "preferred_rate")

        XCTAssertEqual(store.preferredPlaybackRate(), 1.0, accuracy: 0.001)
    }
}
