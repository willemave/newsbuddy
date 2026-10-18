import XCTest

final class DetailReaderUITests: XCTestCase {
    func testDetailOpensOneReadingSurfaceThatFlipsToWebAndCreatesDecks() {
        let app = XCUIApplication()
        app.launchArguments = [
            "-newslyE2EEnabled", "YES",
            "-newslyE2EVisualState", "detail-action-bar"
        ]
        app.launch()

        let reader = app.buttons["content.action.reader"].firstMatch
        XCTAssertTrue(reader.waitForExistence(timeout: 5))
        XCTAssertEqual(reader.label, "Read transcript")
        XCTAssertFalse(app.buttons["content.action.open_external"].exists)
        XCTAssertEqual(app.buttons.matching(identifier: "content.action.reader").count, 2)
        XCTAssertEqual(app.buttons.matching(identifier: "content.action.create_deck").count, 2)
        attachScreenshot(app, named: "detail-action-bars")

        reader.tap()
        XCTAssertTrue(app.staticTexts["article.reader.title"].waitForExistence(timeout: 5))
        let toggle = app.buttons["article.reader.toggle_mode"]
        let deck = app.buttons["article.reader.create_deck"]
        let increase = app.buttons["article.reader.text_size.increase"]
        XCTAssertEqual(toggle.label, "Show original page")
        XCTAssertTrue(deck.exists)
        // The mode toggle owns the toolbar's trailing corner.
        XCTAssertGreaterThan(toggle.frame.minX, deck.frame.maxX)
        XCTAssertGreaterThan(deck.frame.minX, increase.frame.maxX)
        attachScreenshot(app, named: "reader-text")

        toggle.tap()
        XCTAssertTrue(app.descendants(matching: .any)["article.reader.web"].waitForExistence(timeout: 5))
        XCTAssertEqual(toggle.label, "Show reader")
        XCTAssertFalse(increase.exists)
        XCTAssertTrue(deck.exists)
        _ = app.webViews.staticTexts.firstMatch.waitForExistence(timeout: 10)
        attachScreenshot(app, named: "reader-web")

        deck.tap()
        XCTAssertTrue(app.descendants(matching: .any)["learning_deck.create.sheet"].waitForExistence(timeout: 5))
        attachScreenshot(app, named: "reader-deck-sheet")
        app.buttons["learning_deck.create.cancel"].tap()

        toggle.tap()
        XCTAssertTrue(app.staticTexts["article.reader.title"].waitForExistence(timeout: 5))
        XCTAssertTrue(increase.exists)
    }

    func testDetailDeckButtonOpensDeckCreation() {
        let app = XCUIApplication()
        app.launchArguments = [
            "-newslyE2EEnabled", "YES",
            "-newslyE2EVisualState", "detail-action-bar"
        ]
        app.launch()

        let deck = app.buttons["content.action.create_deck"].firstMatch
        XCTAssertTrue(deck.waitForExistence(timeout: 5))
        XCTAssertEqual(deck.label, "Create Learning Deck")
        deck.tap()
        XCTAssertTrue(app.staticTexts["e2e.detail.deck_create"].waitForExistence(timeout: 5))
    }

    private func attachScreenshot(_ app: XCUIApplication, named name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
