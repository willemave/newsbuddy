import XCTest
@testable import newsly

final class BriefingPinnedChromeTests: XCTestCase {
    private let masthead: CGFloat = 110
    private let distance: CGFloat = 150
    private var deep: CGFloat { masthead + distance + 200 }

    private func step(
        hide: CGFloat = 0,
        upwardTravel: CGFloat = 0,
        from last: CGFloat?,
        to scrolled: CGFloat
    ) -> BriefingPinnedChromeStep {
        briefingPinnedChromeStep(
            hide: hide,
            upwardTravel: upwardTravel,
            lastScrolled: last,
            scrolled: scrolled,
            mastheadHeight: masthead,
            hideDistance: distance
        )
    }

    func testChromeStaysWhileMastheadCollapses() {
        XCTAssertEqual(step(from: 40, to: 100).hide, 0)
    }

    func testScrollingPastMastheadCarriesChromeWithTheText() {
        let crossing = step(from: masthead - 20, to: masthead + 30)
        XCTAssertEqual(crossing.hide, 30, "pills start leaving exactly where the masthead finished")
        XCTAssertEqual(step(hide: crossing.hide, from: masthead + 30, to: masthead + 70).hide, 70)
    }

    func testHideStopsAtTheFullDistance() {
        XCTAssertEqual(step(hide: distance - 5, from: deep, to: deep + 40).hide, distance)
    }

    func testDownwardScrollAfterRevealHidesByTheDistanceScrolled() {
        XCTAssertEqual(step(hide: 0, from: deep, to: deep + 12).hide, 12)
    }

    func testSmallUpwardScrollReveals() {
        let jitter = step(hide: distance, from: deep, to: deep - 5)
        XCTAssertEqual(jitter.hide, distance, "jitter under the reveal distance is ignored")
        XCTAssertFalse(jitter.revealed)
        let reveal = step(hide: distance, upwardTravel: jitter.upwardTravel, from: deep - 5, to: deep - 10)
        XCTAssertTrue(reveal.revealed)
        XCTAssertEqual(reveal.hide, 0)
    }

    func testDownwardMoveRestartsUpwardTravel() {
        let up = step(hide: distance, from: deep, to: deep - 6)
        XCTAssertEqual(up.upwardTravel, 6)
        XCTAssertEqual(step(hide: distance, upwardTravel: up.upwardTravel, from: deep - 6, to: deep).upwardTravel, 0)
    }

    func testReturningNearTheTopBringsChromeDownWithTheText() {
        let near = step(hide: 60, from: masthead + 61, to: masthead + 40)
        XCTAssertEqual(near.hide, 0, "past the reveal distance it slides back")
        let small = step(hide: 60, from: masthead + 62, to: masthead + 58)
        XCTAssertEqual(small.hide, 58, "never hidden by more than the distance past the masthead")
        XCTAssertFalse(small.revealed)
    }

    func testFirstSampleOnlySetsBaseline() {
        XCTAssertEqual(step(from: nil, to: 3000).hide, 0)
        XCTAssertEqual(step(hide: distance, from: nil, to: masthead + 20).hide, 20)
    }

    func testSettleFinishesInTheDirectionOfTravel() {
        XCTAssertEqual(
            briefingPinnedChromeSettleTarget(hide: 40, ceiling: distance, hideDistance: distance, movingDown: true),
            distance
        )
        XCTAssertEqual(
            briefingPinnedChromeSettleTarget(hide: 40, ceiling: distance, hideDistance: distance, movingDown: false),
            0
        )
    }

    func testSettleRevealsWhenThereIsNoRoomToHide() {
        XCTAssertEqual(
            briefingPinnedChromeSettleTarget(hide: 40, ceiling: 40, hideDistance: distance, movingDown: true),
            0
        )
    }

    func testSettleLeavesRestingChromeAlone() {
        XCTAssertNil(briefingPinnedChromeSettleTarget(hide: 0, ceiling: distance, hideDistance: distance, movingDown: true))
        XCTAssertNil(briefingPinnedChromeSettleTarget(hide: distance, ceiling: distance, hideDistance: distance, movingDown: false))
    }

    func testHideDistanceClearsStatusBarAndFade() {
        XCTAssertEqual(
            briefingPinnedChromeHideDistance(screenTop: 59, pinnedHeight: 52),
            59 + 52 + BriefingPinnedChromeBacking.fadeHeight + 4
        )
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
            mastheadHeight: masthead,
            reduceMotion: false
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
    func testRevealSlideIsNotOverwrittenByTheNextSamples() {
        let clock = TestClock()
        let model = makeModel(clock: clock)
        track(model, 0)
        track(model, deep)
        track(model, deep - 10)
        XCTAssertEqual(model.pinnedHide(forLens: "news"), 0)

        track(model, deep + 20)
        XCTAssertEqual(model.pinnedHide(forLens: "news"), 0, "downward samples wait for the slide")

        clock.now += BriefingChromeCollapseModel.slideDuration + 0.01
        track(model, deep + 30)
        XCTAssertEqual(model.pinnedHide(forLens: "news"), 10, "then hide from where the reader is")
    }

    @MainActor
    func testSettleOnIdleFinishesAPartialHide() {
        let clock = TestClock()
        let model = makeModel(clock: clock)
        track(model, 0)
        track(model, deep)
        track(model, deep - 10)
        clock.now += 1
        track(model, deep + 30)
        XCTAssertEqual(model.pinnedHide(forLens: "news"), 40)

        model.settlePinnedChrome(forLens: "news", reduceMotion: true)
        XCTAssertTrue(model.isPinnedChromeHidden(forLens: "news"))
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
