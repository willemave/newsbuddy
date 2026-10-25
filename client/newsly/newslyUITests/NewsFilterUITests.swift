import XCTest

final class NewsFilterUITests: XCTestCase {
    func testOnboardingShowsEditableInterestsAndAllowsOptOut() {
        let app = XCUIApplication()
        app.launchArguments = ["-newslyE2EEnabled", "YES", "-newslyE2EVisualState", "onboarding-news-filter"]
        app.launch()
        let editor = app.textViews["onboarding.news_filter.text"]
        XCTAssertTrue(editor.waitForExistence(timeout: 10))
        XCTAssertEqual(editor.value as? String, "Space launches, but never celebrity news or sports.")
        let toggle = app.switches["onboarding.news_filter.enabled"]
        XCTAssertEqual(toggle.value as? String, "1")
        toggle.tap()
        XCTAssertEqual(toggle.value as? String, "0")
        let screenshot = XCTAttachment(screenshot: app.screenshot())
        screenshot.name = "Onboarding-News-filter-opt-out"
        screenshot.lifetime = .keepAlways
        add(screenshot)
    }

    func testDraftPreviewApplyAndRecoverableNewsNavigation() {
        let app = XCUIApplication()
        app.launchArguments = ["-newslyE2EEnabled", "YES", "-newslyE2EVisualState", "news-filter"]
        app.launch()
        let editor = app.textViews["news_filter.text"]
        XCTAssertTrue(editor.waitForExistence(timeout: 10))
        XCTAssertEqual(editor.value as? String,
            "Include space exploration and climate research. Exclude celebrity news and sports.")
        let enabled = app.switches["news_filter.enabled"]
        XCTAssertTrue(enabled.exists)
        let apply = app.buttons["news_filter.apply"]
        XCTAssertFalse(apply.isEnabled)
        app.buttons["news_filter.preview"].tap()
        XCTAssertTrue(app.staticTexts[
            "NASA confirms Artemis III crew for lunar landing, Matches your filter"
        ].waitForExistence(timeout: 3))
        XCTAssertTrue(app.staticTexts[
            "Pop star announces surprise stadium tour, Filtered out of upcoming Briefings"
        ].exists)
        XCTAssertFalse(apply.isEnabled)
        enabled.tap()
        XCTAssertTrue(apply.isEnabled)
        apply.tap()
        let message = app.staticTexts["news_filter.message"]
        XCTAssertTrue(message.waitForExistence(timeout: 3))
        XCTAssertEqual(message.label,
            "Applied to upcoming News additions. Published Briefings stay available.")
        let screenshot = XCTAttachment(screenshot: app.screenshot())
        screenshot.name = "News-filter-editor"
        screenshot.lifetime = .keepAlways
        add(screenshot)
        app.swipeUp()
        app.buttons["Filtered Out"].tap()
        XCTAssertTrue(app.staticTexts["No News is currently filtered out."].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Edit News Filter"].exists)
    }
}
