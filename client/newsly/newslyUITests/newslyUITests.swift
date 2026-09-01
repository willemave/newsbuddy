import Foundation
import XCTest

final class newslyUITests: XCTestCase {
    private var e2eServerPort: String {
        (Bundle(for: type(of: self)).object(
            forInfoDictionaryKey: "NEWSLY_E2E_SERVER_PORT"
        ) as? String).flatMap { $0.isEmpty ? nil : $0 } ?? "8000"
    }

    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    @MainActor
    func testAppLaunches() throws {
        let app = XCUIApplication()
        app.launch()

        XCTAssertTrue(app.wait(for: .runningForeground, timeout: 10))
    }

    @MainActor
    func testWarmResumeReturnsForegroundWithoutRelaunch() async throws {
        let app = try await makeAuthenticatedApp()
        app.launch()
        XCTAssertTrue(app.wait(for: .runningForeground, timeout: 10))
        assertAuthenticatedBriefingRoot(in: app, phase: "initial launch")

        XCUIDevice.shared.press(.home)
        let enteredBackground = app.wait(for: .runningBackground, timeout: 1)
            || app.wait(for: .runningBackgroundSuspended, timeout: 5)
        XCTAssertTrue(enteredBackground)

        app.activate()
        XCTAssertTrue(app.wait(for: .runningForeground, timeout: 10))
        assertAuthenticatedBriefingRoot(in: app, phase: "warm resume")
    }

    @MainActor
    func testProcessReclaimedRelaunchReturnsForeground() async throws {
        let app = try await makeAuthenticatedApp()
        app.launch()
        XCTAssertTrue(app.wait(for: .runningForeground, timeout: 10))
        assertAuthenticatedBriefingRoot(in: app, phase: "initial launch")

        app.terminate()
        XCTAssertTrue(app.wait(for: .notRunning, timeout: 5))

        try setAutoLogin(false, for: app)
        app.launch()
        XCTAssertTrue(app.wait(for: .runningForeground, timeout: 10))
        assertAuthenticatedBriefingRoot(in: app, phase: "process relaunch")
    }

    @MainActor
    private func makeAuthenticatedApp() async throws -> XCUIApplication {
        let userID = try await requireLocalDebugUser()
        let app = XCUIApplication()
        app.launchArguments = [
            "-newslyE2EEnabled", "true",
            "-newslyE2EAutoLogin", "true",
            "-newslyE2EServerHost", "127.0.0.1",
            "-newslyE2EServerPort", e2eServerPort,
            "-newslyE2EUseHTTPS", "false",
            "-newslyE2EUserId", String(userID),
            "-newslyE2ECompleteOnboarding", "true",
            "-newslyE2ECompleteTutorial", "true",
        ]
        return app
    }

    private func requireLocalDebugUser() async throws -> Int {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 5
        configuration.timeoutIntervalForResource = 10
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }

        var request = URLRequest(
            url: URL(string: "http://127.0.0.1:\(e2eServerPort)/auth/debug/new-user")!
        )
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(
            DebugUserRequest(
                hasCompletedOnboarding: true,
                hasCompletedNewUserTutorial: true
            )
        )

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch {
            throw XCTSkip(
                "Authenticated lifecycle UI tests require the local debug API at "
                    + "http://127.0.0.1:\(e2eServerPort) (\(error.localizedDescription))."
            )
        }

        guard let httpResponse = response as? HTTPURLResponse,
              (200..<300).contains(httpResponse.statusCode) else {
            let statusCode = (response as? HTTPURLResponse)?.statusCode ?? -1
            throw XCTSkip(
                "Authenticated lifecycle UI tests require a development-mode local API; "
                    + "debug-session setup returned HTTP \(statusCode)."
            )
        }

        let setup = try XCTUnwrap(
            try? JSONDecoder().decode(DebugUserResponse.self, from: data),
            "The local debug API returned an invalid successful debug-session response."
        )
        return setup.user.id
    }

    @MainActor
    private func setAutoLogin(_ enabled: Bool, for app: XCUIApplication) throws {
        var arguments = app.launchArguments
        let keyIndex = try XCTUnwrap(
            arguments.firstIndex(of: "-newslyE2EAutoLogin"),
            "The authenticated lifecycle launch must configure E2E auto-login."
        )
        let valueIndex = arguments.index(after: keyIndex)
        guard arguments.indices.contains(valueIndex) else {
            XCTFail("The E2E auto-login launch argument is missing its value.")
            return
        }
        arguments[valueIndex] = enabled ? "true" : "false"
        app.launchArguments = arguments
    }

    @MainActor
    private func assertAuthenticatedBriefingRoot(
        in app: XCUIApplication,
        phase: String,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        let briefingScreen = app.descendants(matching: .any)["briefing.screen"].firstMatch
        XCTAssertTrue(
            briefingScreen.waitForExistence(timeout: 20),
            "Expected the authenticated Briefing root after \(phase).",
            file: file,
            line: line
        )
        guard briefingScreen.exists else { return }

        let blockingError = app.descendants(matching: .any)["briefing.blocking_error"].firstMatch
        XCTAssertFalse(
            blockingError.waitForExistence(timeout: 2),
            "A blocking Try Again state replaced the authenticated root after \(phase).",
            file: file,
            line: line
        )
        XCTAssertFalse(
            app.descendants(matching: .any)["auth.landing.screen"].firstMatch.exists,
            "Authentication fell back to the landing screen after \(phase).",
            file: file,
            line: line
        )
        XCTAssertTrue(
            briefingScreen.exists,
            "The authenticated Briefing root did not remain visible after \(phase).",
            file: file,
            line: line
        )
    }
}

private struct DebugUserRequest: Encodable {
    let hasCompletedOnboarding: Bool
    let hasCompletedNewUserTutorial: Bool

    enum CodingKeys: String, CodingKey {
        case hasCompletedOnboarding = "has_completed_onboarding"
        case hasCompletedNewUserTutorial = "has_completed_new_user_tutorial"
    }
}

private struct DebugUserResponse: Decodable {
    struct User: Decodable {
        let id: Int
    }

    let user: User
}
