import Foundation
import XCTest
@testable import newsly

final class XIntegrationServiceTests: XCTestCase {
    func testConnectionPresentationMarksReauthAsReconnectRequired() {
        let response = APIXConnectionResponse(
            provider: "x",
            connected: false,
            isActive: false,
            providerUserId: "123",
            providerUsername: "willemaw",
            scopes: ["tweet.read", "users.read", "bookmark.read", "offline.access"],
            lastSyncedAt: nil,
            lastStatus: "failed",
            lastError: "X API 400: invalid_request",
            twitterUsername: nil
        )

        XCTAssertTrue(response.needsAttention)
        XCTAssertEqual(response.settingsSubtitle, "Reconnect required")
        XCTAssertEqual(response.connectActionTitle, "Reconnect X")
        XCTAssertEqual(
            response.issueMessage,
            "Your saved X session expired or was rejected. Reconnect your account to restore bookmark sync."
        )
        XCTAssertEqual(response.issueDetails, "X API 400: invalid_request")
    }

    func testConnectionPresentationPrefersUsernameWhenHealthy() {
        let response = APIXConnectionResponse(
            provider: "x",
            connected: true,
            isActive: true,
            providerUserId: "123",
            providerUsername: "willemaw",
            scopes: ["tweet.read", "users.read", "bookmark.read", "offline.access"],
            lastSyncedAt: nil,
            lastStatus: "success",
            lastError: nil,
            twitterUsername: nil
        )

        XCTAssertFalse(response.needsAttention)
        XCTAssertEqual(response.settingsSubtitle, "@willemaw")
        XCTAssertEqual(response.connectActionTitle, "Connect X")
    }

    func testUserInitiatedDisconnectDoesNotNeedAttention() {
        let response = APIXConnectionResponse(
            provider: "x",
            connected: false,
            isActive: false,
            providerUserId: "123",
            providerUsername: "willemaw",
            scopes: [],
            lastSyncedAt: nil,
            lastStatus: "disconnected",
            lastError: nil,
            twitterUsername: nil
        )

        XCTAssertFalse(response.needsAttention)
    }

    func testStartOAuthStartsWithoutUserEnteredUsername() async throws {
        let client = MockXIntegrationAPIClient()
        client.startResponse = APIXOAuthStartResponse(
            authorizeUrl: "https://x.com/i/oauth2/authorize?state=test",
            state: "test",
            scopes: ["tweet.read", "users.read", "bookmark.read"]
        )
        let service = XIntegrationService(client: client)

        let response = try await service.startOAuth()

        XCTAssertEqual(response.state, "test")
        XCTAssertEqual(client.startOAuthCallCount, 1)
    }

    @MainActor
    func testConnectViaOAuthExchangesCodeFromCallback() async throws {
        let client = MockXIntegrationAPIClient()
        client.startResponse = APIXOAuthStartResponse(
            authorizeUrl: "https://x.com/i/oauth2/authorize?state=start-state",
            state: "start-state",
            scopes: ["tweet.read", "users.read"]
        )
        client.exchangeResponse = APIXConnectionResponse(
            provider: "x",
            connected: true,
            isActive: true,
            providerUserId: "123",
            providerUsername: "willemaw",
            scopes: ["tweet.read", "users.read"],
            lastSyncedAt: nil,
            lastStatus: "connected",
            lastError: nil,
            twitterUsername: nil
        )
        let service = XIntegrationService(
            client: client,
            oauthSessionHandler: { authorizeURL in
                XCTAssertEqual(
                    authorizeURL.absoluteString,
                    "https://x.com/i/oauth2/authorize?state=start-state"
                )
                return try XCTUnwrap(
                    URL(string: "newsly://oauth?code=oauth-code&state=oauth-state")
                )
            }
        )

        let response = try await service.connectViaOAuth()

        XCTAssertTrue(response.connected)
        XCTAssertEqual(client.startOAuthCallCount, 1)
        XCTAssertEqual(client.recordedExchangeCode, "oauth-code")
        XCTAssertEqual(client.recordedExchangeState, "oauth-state")
    }

    @MainActor
    func testConnectViaOAuthThrowsWhenCallbackContainsOAuthError() async throws {
        let client = MockXIntegrationAPIClient()
        client.startResponse = APIXOAuthStartResponse(
            authorizeUrl: "https://x.com/i/oauth2/authorize?state=start-state",
            state: "start-state",
            scopes: ["tweet.read", "users.read"]
        )
        let service = XIntegrationService(
            client: client,
            oauthSessionHandler: { _ in
                try XCTUnwrap(
                    URL(
                        string: "newsly://oauth?error=access_denied&error_description=User%20denied"
                    )
                )
            }
        )

        do {
            _ = try await service.connectViaOAuth()
            XCTFail("Expected oauth failure")
        } catch let error as XIntegrationError {
            XCTAssertEqual(error.localizedDescription, "OAuth failed: User denied")
        }
    }

    @MainActor
    func testConnectViaOAuthThrowsWhenCallbackMissingCode() async throws {
        let client = MockXIntegrationAPIClient()
        client.startResponse = APIXOAuthStartResponse(
            authorizeUrl: "https://x.com/i/oauth2/authorize?state=start-state",
            state: "start-state",
            scopes: ["tweet.read", "users.read"]
        )
        let service = XIntegrationService(
            client: client,
            oauthSessionHandler: { _ in
                try XCTUnwrap(URL(string: "newsly://oauth?state=oauth-state"))
            }
        )

        do {
            _ = try await service.connectViaOAuth()
            XCTFail("Expected missing callback code failure")
        } catch let error as XIntegrationError {
            XCTAssertEqual(error.localizedDescription, "OAuth callback missing code")
        }
    }

    @MainActor
    func testConnectViaOAuthThrowsWhenCallbackMissingState() async throws {
        let client = MockXIntegrationAPIClient()
        client.startResponse = APIXOAuthStartResponse(
            authorizeUrl: "https://x.com/i/oauth2/authorize?state=start-state",
            state: "start-state",
            scopes: ["tweet.read", "users.read"]
        )
        let service = XIntegrationService(
            client: client,
            oauthSessionHandler: { _ in
                try XCTUnwrap(URL(string: "newsly://oauth?code=oauth-code"))
            }
        )

        do {
            _ = try await service.connectViaOAuth()
            XCTFail("Expected missing callback state failure")
        } catch let error as XIntegrationError {
            XCTAssertEqual(error.localizedDescription, "OAuth callback missing state")
        }
    }

    @MainActor
    func testConnectViaOAuthThrowsWhenAuthorizeURLIsInvalid() async throws {
        let client = MockXIntegrationAPIClient()
        client.startResponse = APIXOAuthStartResponse(
            authorizeUrl: "https://[::1",
            state: "start-state",
            scopes: ["tweet.read", "users.read"]
        )
        let service = XIntegrationService(client: client)

        do {
            _ = try await service.connectViaOAuth()
            XCTFail("Expected invalid authorize URL failure")
        } catch let error as XIntegrationError {
            XCTAssertEqual(error.localizedDescription, "Invalid OAuth authorize URL")
        }
    }
}

private final class MockXIntegrationAPIClient: XIntegrationAPIClientProtocol {
    var startResponse: APIXOAuthStartResponse?
    var exchangeResponse: APIXConnectionResponse?
    private(set) var startOAuthCallCount = 0
    private(set) var recordedExchangeCode: String?
    private(set) var recordedExchangeState: String?

    func fetchConnection() async throws -> APIXConnectionResponse {
        throw XIntegrationError.callbackParsingFailed
    }

    func startOAuth() async throws -> APIXOAuthStartResponse {
        startOAuthCallCount += 1
        return try XCTUnwrap(startResponse)
    }

    func exchangeOAuth(code: String, state: String) async throws -> APIXConnectionResponse {
        recordedExchangeCode = code
        recordedExchangeState = state
        return try XCTUnwrap(exchangeResponse)
    }

    func disconnect() async throws {}
}
