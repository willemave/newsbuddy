import XCTest
@testable import newsly

final class BriefingPinnedChromeTests: XCTestCase {
    private let masthead: CGFloat = 110
    private let distance: CGFloat = 150
    private var deep: CGFloat { masthead + distance + 200 }

    private func hide(
        _ hide: CGFloat = 0,
        from last: CGFloat?,
        to scrolled: CGFloat
    ) -> CGFloat {
        briefingPinnedChromeHide(
            hide: hide,
            lastScrolled: last,
            scrolled: scrolled,
            mastheadHeight: masthead,
            hideDistance: distance
        )
    }

    func testChromeStaysWhileMastheadCollapses() {
        XCTAssertEqual(hide(from: 40, to: 100), 0)
    }

    func testScrollingPastMastheadCarriesChromeWithTheText() {
        let crossing = hide(from: masthead - 20, to: masthead + 30)
        XCTAssertEqual(crossing, 30, "pills start leaving exactly where the masthead finished")
        XCTAssertEqual(hide(crossing, from: masthead + 30, to: masthead + 70), 70)
    }

    func testHideStopsAtTheFullDistance() {
        XCTAssertEqual(hide(distance - 5, from: deep, to: deep + 40), distance)
    }

    func testDownwardScrollAfterRevealHidesByTheDistanceScrolled() {
        XCTAssertEqual(hide(0, from: deep, to: deep + 12), 12)
    }

    func testUpwardScrollDoesNotBringChromeBack() {
        XCTAssertEqual(hide(distance, from: deep, to: deep - 200), distance)
    }

    func testReturningNearTheTopBringsChromeDownWithTheText() {
        XCTAssertEqual(hide(distance, from: masthead + 61, to: masthead + 40), 40)
        XCTAssertEqual(hide(distance, from: masthead + 40, to: masthead - 10), 0)
    }

    func testFirstSampleOnlySetsBaseline() {
        XCTAssertEqual(hide(from: nil, to: 3000), 0)
        XCTAssertEqual(hide(distance, from: nil, to: masthead + 20), 20)
    }

    func testHideDistanceClearsStatusBar() {
        XCTAssertEqual(briefingPinnedChromeHideDistance(screenTop: 59, pinnedHeight: 52), 59 + 52 + 4)
    }

    // MARK: Model

    @MainActor
    private func makeModel(clock: TestClock) -> BriefingChromeCollapseModel {
        let model = BriefingChromeCollapseModel(now: { clock.now })
        model.setPinnedHideDistance(distance)
        return model
    }

    @MainActor
    private func track(_ model: BriefingChromeCollapseModel, _ scrolled: CGFloat) {
        model.trackPinnedChrome(
            forLens: "news",
            scrolled: scrolled,
            mastheadHeight: masthead
        )
    }

    @MainActor
    func testModelHidesContinuouslyAndReportsFullyHidden() {
        let clock = TestClock()
        let model = makeModel(clock: clock)
        track(model, 0)
        track(model, masthead + 50)
        XCTAssertEqual(model.pinnedHide(forLens: "news"), 50)
        XCTAssertFalse(model.isPinnedChromeHidden(forLens: "news"))
        track(model, deep)
        XCTAssertEqual(model.pinnedHide(forLens: "news"), distance)
        XCTAssertTrue(model.isPinnedChromeHidden(forLens: "news"))
    }

    @MainActor
    func testScrollingUpKeepsChromeHiddenUntilTheTop() {
        let clock = TestClock()
        let model = makeModel(clock: clock)
        track(model, 0)
        track(model, deep)
        track(model, deep - 100)
        XCTAssertTrue(model.isPinnedChromeHidden(forLens: "news"))
        track(model, masthead + 30)
        XCTAssertEqual(model.pinnedHide(forLens: "news"), 30)
        track(model, 0)
        XCTAssertEqual(model.pinnedHide(forLens: "news"), 0)
    }

    @MainActor
    func testPageRevealSlideIsNotOverwrittenByTheNextSamples() {
        let clock = TestClock()
        let model = makeModel(clock: clock)
        track(model, 0)
        track(model, deep)
        model.revealPinnedChrome(forLens: "news", reduceMotion: false)
        track(model, deep + 20)
        XCTAssertEqual(model.pinnedHide(forLens: "news"), 0, "downward samples wait for the slide")

        clock.now += BriefingChromeCollapseModel.slideDuration + 0.01
        track(model, deep + 30)
        XCTAssertEqual(model.pinnedHide(forLens: "news"), 10, "then hide from where the reader is")
    }

    @MainActor
    func testFullyHiddenChromeStaysHiddenWhenItGrows() {
        let clock = TestClock()
        let model = makeModel(clock: clock)
        track(model, 0)
        track(model, deep)
        XCTAssertTrue(model.isPinnedChromeHidden(forLens: "news"))

        // The header grows with no scroll sample in between.
        model.setPinnedHideDistance(distance + 40)
        XCTAssertEqual(model.pinnedHide(forLens: "news"), distance + 40)
        XCTAssertTrue(model.isPinnedChromeHidden(forLens: "news"))
    }

    @MainActor
    func testPartlyHiddenChromeClampsWhenItShrinks() {
        let clock = TestClock()
        let model = makeModel(clock: clock)
        track(model, 0)
        track(model, masthead + 100)
        model.setPinnedHideDistance(80)
        XCTAssertEqual(model.pinnedHide(forLens: "news"), 80)
    }

    @MainActor
    func testResetShowsChromeImmediately() {
        let clock = TestClock()
        let model = makeModel(clock: clock)
        track(model, 0)
        track(model, deep)
        model.resetCollapse(forLens: "news")
        XCTAssertEqual(model.pinnedHide(forLens: "news"), 0)
        XCTAssertFalse(model.isPinnedChromeHidden(forLens: "news"))
    }
}

private final class TestClock {
    var now: TimeInterval = 100
}
