import XCTest

final class LearningDeckChatLayoutUITests: XCTestCase {
    func testComposerAndVoiceButtonStayAboveKeyboard() {
        let app = launchReader()
        app.buttons["learning_deck.chat.peek"].tap()
        let input = app.textFields["learning_deck.chat.input"]
        XCTAssertTrue(input.waitForExistence(timeout: 5))
        input.tap()
        input.typeText("Why does sampling help?")

        let keyboard = app.keyboards.firstMatch
        XCTAssertTrue(keyboard.waitForExistence(timeout: 5))
        let voice = app.buttons["learning_deck.chat.voice"]
        let send = app.buttons["learning_deck.chat.send"]
        XCTAssertTrue(voice.isHittable)
        XCTAssertTrue(send.isHittable)
        XCTAssertLessThanOrEqual(input.frame.maxY, keyboard.frame.minY)
        XCTAssertLessThanOrEqual(voice.frame.maxY, keyboard.frame.minY)
        XCTAssertLessThanOrEqual(send.frame.maxY, keyboard.frame.minY)
        XCTAssertLessThan(keyboard.frame.minY - input.frame.maxY, 90)
        XCTAssertGreaterThan(input.frame.minY, 100)
        attachScreenshot(app, name: "deck-chat-keyboard")

        voice.tap()
        expectation(for: NSPredicate(format: "label == %@", "Stop voice recording"), evaluatedWith: voice)
        waitForExpectations(timeout: 5)
        XCTAssertTrue(keyboard.waitForNonExistence(timeout: 5))
        XCTAssertEqual(input.value as? String, "Why does sampling help?")

        app.buttons["learning_deck.chat.collapse"].tap()
        XCTAssertTrue(app.buttons["learning_deck.chat.peek"].waitForExistence(timeout: 5))
        XCTAssertTrue(voice.isHittable)
        XCTAssertTrue(keyboard.waitForNonExistence(timeout: 5))
        attachScreenshot(app, name: "deck-chat-peek")
        app.terminate()
    }

    func testVoiceStartsFromBottomBarWithoutKeyboard() {
        let app = launchReader()
        let voice = app.buttons["learning_deck.chat.voice"]
        XCTAssertTrue(voice.waitForExistence(timeout: 5))
        voice.tap()
        let recording = NSPredicate(format: "label == %@", "Stop voice recording")
        expectation(for: recording, evaluatedWith: voice)
        waitForExpectations(timeout: 5)
        XCTAssertEqual(app.keyboards.count, 0)
        XCTAssertTrue(app.staticTexts["learning_deck.chat.voice_status"].exists)
        attachScreenshot(app, name: "deck-chat-recording")
        app.terminate()
    }

    func testLandscapeComposerAndVoiceButtonFitAboveKeyboard() {
        XCUIDevice.shared.orientation = .landscapeLeft
        defer { XCUIDevice.shared.orientation = .portrait }
        let app = launchReader(waitForPeek: false)
        let openChat = app.buttons["Open chat"]
        XCTAssertTrue(openChat.waitForExistence(timeout: 5))
        openChat.tap()
        let input = app.textFields["learning_deck.chat.input"]
        XCTAssertTrue(input.waitForExistence(timeout: 5))
        input.tap()
        input.typeText("Explain this")
        let keyboard = app.keyboards.firstMatch
        XCTAssertTrue(keyboard.waitForExistence(timeout: 5))
        let voice = app.buttons["learning_deck.chat.voice"]
        XCTAssertTrue(voice.isHittable)
        XCTAssertTrue(app.buttons["learning_deck.chat.send"].isHittable)
        XCTAssertLessThanOrEqual(input.frame.maxY, keyboard.frame.minY)
        XCTAssertLessThanOrEqual(voice.frame.maxY, keyboard.frame.minY)
        XCTAssertLessThan(keyboard.frame.minY - input.frame.maxY, 90)
        attachScreenshot(app, name: "deck-chat-landscape-keyboard")
        app.terminate()
    }

    private func launchReader(waitForPeek: Bool = true) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments = [
            "-newslyE2EEnabled", "YES",
            "-newslyE2EVisualState", "learning-deck-reader",
            "-newslyE2EFakeSpeechEnabled", "YES"
        ]
        app.launch()
        if waitForPeek {
            XCTAssertTrue(app.buttons["learning_deck.chat.peek"].waitForExistence(timeout: 5))
        }
        return app
    }

    private func attachScreenshot(_ app: XCUIApplication, name: String) {
        let attachment = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
