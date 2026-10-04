import XCTest

final class KnowledgeSourceActivityUITests: XCTestCase {
    func testSourceRowsRefreshWhenRelatedWorkFinishesAndRestarts() {
        let app = XCUIApplication()
        app.launchArguments = [
            "-newslyE2EEnabled", "YES",
            "-newslyE2EVisualState", "knowledge-source-activity"
        ]
        app.launch()

        let deckRow = app.buttons["saved-901"]
        let chatRow = app.buttons["saved-902"]
        XCTAssertTrue(deckRow.waitForExistence(timeout: 5))
        XCTAssertTrue(chatRow.exists)
        assertOnlySourceRows(in: app)
        XCTAssertTrue(deckRow.label.contains("DECK GENERATING"))
        XCTAssertTrue(chatRow.label.contains("CHAT ANSWERING"))
        XCTAssertTrue(deckRow.label.contains("Processing"))
        XCTAssertTrue(chatRow.label.contains("Processing"))
        assertOnlySourceRows(in: app)

        app.buttons["knowledge.source_activity.finish"].tap()
        XCTAssertTrue(deckRow.label.contains("DECK READY"))
        XCTAssertFalse(chatRow.label.contains("CHAT ANSWERING"))
        XCTAssertFalse(deckRow.label.contains("Processing"))
        XCTAssertFalse(chatRow.label.contains("Processing"))

        // Chat summaries have equality by ID; changing an existing chat's state
        // must still redraw the row instead of retaining its idle artwork.
        app.buttons["knowledge.source_activity.start"].tap()
        XCTAssertTrue(deckRow.label.contains("DECK GENERATING"))
        XCTAssertTrue(chatRow.label.contains("CHAT ANSWERING"))
        XCTAssertTrue(deckRow.label.contains("Processing"))
        XCTAssertTrue(chatRow.label.contains("Processing"))

        app.buttons["knowledge.source_activity.fail"].tap()
        XCTAssertTrue(deckRow.label.contains("DECK FAILED"))
        XCTAssertFalse(deckRow.label.contains("Processing"))
        XCTAssertFalse(chatRow.label.contains("Processing"))
        assertOnlySourceRows(in: app)
    }

    func testRelatedMenusOpenAndDeleteArtifactsWithoutRemovingSources() {
        let app = XCUIApplication()
        app.launchArguments = [
            "-newslyE2EEnabled", "YES",
            "-newslyE2EVisualState", "knowledge-source-activity"
        ]
        app.launch()
        let deckRow = app.buttons["saved-901"]
        let chatRow = app.buttons["saved-902"]
        XCTAssertTrue(deckRow.waitForExistence(timeout: 5))

        deckRow.press(forDuration: 1)
        XCTAssertFalse(app.buttons["knowledge.deck.903.regenerate"].isEnabled)
        app.buttons["knowledge.deck.903.open"].tap()
        XCTAssertEqual(app.staticTexts["knowledge.source_activity.opened"].label, "Deck opened")

        chatRow.press(forDuration: 1)
        app.buttons["knowledge.chat.904.menu"].tap()
        app.buttons["knowledge.chat.904.open"].tap()
        XCTAssertEqual(app.staticTexts["knowledge.source_activity.opened"].label, "Chat opened")

        chatRow.press(forDuration: 1)
        app.buttons["knowledge.chat.904.menu"].tap()
        app.buttons["knowledge.chat.904.delete"].tap()
        XCTAssertTrue(chatRow.exists)
        XCTAssertFalse(chatRow.label.contains("Processing"))
        XCTAssertFalse(chatRow.value as? String == "Has chat")

        deckRow.press(forDuration: 1)
        app.buttons["knowledge.deck.903.delete"].tap()
        XCTAssertTrue(deckRow.exists)
        XCTAssertFalse(deckRow.label.contains("DECK"))
        assertOnlySourceRows(in: app)
    }

    private func assertOnlySourceRows(in app: XCUIApplication, file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertEqual(
            app.buttons.matching(NSPredicate(format: "identifier IN %@", ["saved-901", "saved-902", "chat-904", "deck-903"])).count,
            2, file: file, line: line
        )
        XCTAssertFalse(app.buttons["chat-904"].exists, file: file, line: line)
        XCTAssertFalse(app.buttons["deck-903"].exists, file: file, line: line)
    }
}
