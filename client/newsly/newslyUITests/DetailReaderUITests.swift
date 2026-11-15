import XCTest

final class DetailReaderUITests: XCTestCase {
    func testDetailOpensOneReadingSurfaceThatFlipsToWebAndCreatesDecks() {
        let app = launchDetailActionBars()

        let reader = app.buttons["content.action.reader"].firstMatch
        XCTAssertTrue(reader.waitForExistence(timeout: 5))
        XCTAssertEqual(reader.label, "Read transcript")
        XCTAssertFalse(app.buttons["content.action.open_external"].exists)
        XCTAssertEqual(app.buttons.matching(identifier: "content.action.reader").count, 3)
        XCTAssertEqual(app.buttons.matching(identifier: "content.action.create_deck").count, 3)
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
        let app = launchDetailActionBars()

        let deck = app.buttons.matching(identifier: "content.action.create_deck").element(boundBy: 1)
        XCTAssertTrue(deck.waitForExistence(timeout: 5))
        XCTAssertEqual(deck.label, "Create Learning Deck")
        deck.tap()
        XCTAssertTrue(app.descendants(matching: .any)["learning_deck.create.sheet"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["A calmer way to follow the news"].exists)
    }

    func testNewsDeckButtonSitsBesideBuddyAndOpensDeckCreation() {
        let app = launchDetailActionBars()

        let deck = app.buttons.matching(identifier: "content.action.create_deck").element(boundBy: 2)
        let buddy = app.buttons.matching(identifier: "content.action.knowledge_actions").element(boundBy: 2)
        let play = app.buttons.matching(identifier: "content.action.podcast_audio").element(boundBy: 2)
        XCTAssertTrue(deck.waitForExistence(timeout: 5))
        XCTAssertEqual(deck.label, "Create Learning Deck")
        XCTAssertEqual(deck.frame.midY, buddy.frame.midY, accuracy: 1)
        XCTAssertGreaterThan(deck.frame.minX, play.frame.maxX)
        XCTAssertGreaterThan(buddy.frame.minX, deck.frame.maxX)

        deck.tap()
        let sheet = app.descendants(matching: .any)["learning_deck.create.sheet"]
        XCTAssertTrue(sheet.waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["A new way to improve video generation"].exists)
        XCTAssertTrue(app.descendants(matching: .any)["learning_deck.create.focus"].exists)
        XCTAssertTrue(app.buttons["learning_deck.focus_mic"].exists)
        XCTAssertTrue(app.buttons["learning_deck.create.submit"].isEnabled)
        attachScreenshot(app, named: "news-deck-sheet")
        app.buttons["learning_deck.create.cancel"].tap()
        XCTAssertTrue(sheet.waitForNonExistence(timeout: 5))
        XCTAssertTrue(deck.isHittable)
    }

    private func launchDetailActionBars() -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments = [
            "-newslyE2EEnabled", "YES",
            "-newslyE2EVisualState", "detail-action-bar"
        ]
        app.launch()
        return app
    }

    private func attachScreenshot(_ app: XCUIApplication, named name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
