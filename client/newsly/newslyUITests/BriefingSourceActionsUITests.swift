import XCTest

final class BriefingSourceActionsUITests: XCTestCase {
    func testInlineSourceMenusInEveryBriefingTier() {
        let app = launchFixture()
        for (tier, key) in [("News", "news:42"), ("Articles", "content:42"), ("Podcasts", "content:42")] {
            app.buttons[tier].tap()
            let passage = app.descendants(matching: .any)["briefing.passage"].firstMatch
            XCTAssertTrue(passage.waitForExistence(timeout: 5))
            // UIKit exposes the passage as a single accessibility element; the
            // source link starts its first line and has no separate selector.
            let link = passage.coordinate(withNormalizedOffset: CGVector(dx: 0, dy: 0))
                .withOffset(CGVector(dx: 40, dy: 20))
            for action in ["Add to Knowledge", "Chat", "Create Deck"] {
                link.press(forDuration: 1)
                for title in ["Add to Knowledge", "Chat", "Create Deck"] {
                    XCTAssertTrue(app.buttons[title].waitForExistence(timeout: 3), "\(tier): \(title)")
                }
                attachScreenshot(app, name: "\(tier)-\(action)")
                app.buttons[action].tap()
                XCTAssertEqual(app.staticTexts["briefing.source_actions.result"].label, "\(action): \(key)")
            }
        }
    }

    func testInlineSourceTapsOpenSource() {
        let app = launchFixture()
        let passage = app.descendants(matching: .any)["briefing.passage"].firstMatch
        XCTAssertTrue(passage.waitForExistence(timeout: 5))
        print("Passage frame: \(passage.frame)")
        passage.coordinate(withNormalizedOffset: CGVector(dx: 0, dy: 0))
            .withOffset(CGVector(dx: 40, dy: 20)).press(forDuration: 0.1)
        let opened = XCTNSPredicateExpectation(
            predicate: NSPredicate(format: "label == %@", "Open news:42"),
            object: app.staticTexts["briefing.source_actions.result"]
        )
        XCTAssertEqual(XCTWaiter.wait(for: [opened], timeout: 3), .completed)
    }

    func testArticleAndPodcastHeadlinesOfferSourceActions() {
        let app = launchFixture()
        for tier in ["Articles", "Podcasts"] {
            app.buttons[tier].tap()
            let headline = app.buttons.matching(NSPredicate(
                format: "label CONTAINS %@", tier == "Articles" ? "Linked article" : "Linked podcast"
            )).firstMatch
            XCTAssertTrue(headline.waitForExistence(timeout: 5))
            headline.press(forDuration: 1)
            XCTAssertTrue(app.buttons["Chat"].waitForExistence(timeout: 3))
            app.buttons["Chat"].tap()
            XCTAssertEqual(app.staticTexts["briefing.source_actions.result"].label, "Chat: content:42")
            headline.tap()
            XCTAssertEqual(app.staticTexts["briefing.source_actions.result"].label, "Open content:42")
        }
    }

    func testNonLinkTextSelectionRetainsDigDeeper() {
        let app = launchFixture()
        let passage = app.descendants(matching: .any)["briefing.passage"].firstMatch
        XCTAssertTrue(passage.waitForExistence(timeout: 5))
        passage.coordinate(withNormalizedOffset: CGVector(dx: 0, dy: 0))
            .withOffset(CGVector(dx: 160, dy: 40)).press(forDuration: 1)
        let digDeeper = app.descendants(matching: .any)["Dig Deeper"].firstMatch
        XCTAssertTrue(digDeeper.waitForExistence(timeout: 5))
        digDeeper.tap()
        XCTAssertEqual(app.staticTexts["briefing.source_actions.result"].label, "Dig Deeper")
    }

    private func launchFixture() -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments = [
            "-newslyE2EEnabled", "YES", "-newslyE2EVisualState", "briefing-source-actions"
        ]
        app.launch()
        return app
    }

    private func attachScreenshot(_ app: XCUIApplication, name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
