import Foundation
import XCTest
@testable import newsly

final class XIntegrationServiceTests: XCTestCase {
    func testStartOAuthNormalizesUsernameBeforeSending() async throws {
        let client = MockXIntegrationAPIClient()
        client.startResponse = XOAuthStartResponse(
            authorizeURL: "https://x.com/i/oauth2/authorize?state=test",
            state: "test",
            scopes: ["tweet.read", "users.read", "bookmark.read", "follows.read", "list.read"]
        )
        let service = XIntegrationService(client: client)

        let response = try await service.startOAuth(twitterUsername: "@willem_aw")

        XCTAssertEqual(response.state, "test")
        XCTAssertEqual(client.recordedStartUsernames, ["willem_aw"])
    }

    @MainActor
    func testConnectViaOAuthExchangesCodeFromCallback() async throws {
        let client = MockXIntegrationAPIClient()
        client.startResponse = XOAuthStartResponse(
            authorizeURL: "https://x.com/i/oauth2/authorize?state=start-state",
            state: "start-state",
            scopes: ["tweet.read", "users.read"]
        )
        client.exchangeResponse = XConnectionResponse(
            provider: "x",
            connected: true,
            isActive: true,
            providerUserID: "123",
            providerUsername: "willemaw",
            scopes: ["tweet.read", "users.read"],
            lastSyncedAt: nil,
            lastStatus: "connected",
            lastError: nil,
            twitterUsername: "willemaw"
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

        let response = try await service.connectViaOAuth(twitterUsername: "willem_aw")

        XCTAssertTrue(response.connected)
        XCTAssertEqual(client.recordedStartUsernames, ["willem_aw"])
        XCTAssertEqual(client.recordedExchangeCode, "oauth-code")
        XCTAssertEqual(client.recordedExchangeState, "oauth-state")
    }

    @MainActor
    func testConnectViaOAuthThrowsWhenCallbackContainsOAuthError() async throws {
        let client = MockXIntegrationAPIClient()
        client.startResponse = XOAuthStartResponse(
            authorizeURL: "https://x.com/i/oauth2/authorize?state=start-state",
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
            _ = try await service.connectViaOAuth(twitterUsername: nil)
            XCTFail("Expected oauth failure")
        } catch let error as XIntegrationError {
            XCTAssertEqual(error.localizedDescription, "OAuth failed: User denied")
        }
    }

    @MainActor
    func testConnectViaOAuthThrowsWhenCallbackMissingCode() async throws {
        let client = MockXIntegrationAPIClient()
        client.startResponse = XOAuthStartResponse(
            authorizeURL: "https://x.com/i/oauth2/authorize?state=start-state",
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
            _ = try await service.connectViaOAuth(twitterUsername: nil)
            XCTFail("Expected missing callback code failure")
        } catch let error as XIntegrationError {
            XCTAssertEqual(error.localizedDescription, "OAuth callback missing code")
        }
    }

    @MainActor
    func testConnectViaOAuthThrowsWhenCallbackMissingState() async throws {
        let client = MockXIntegrationAPIClient()
        client.startResponse = XOAuthStartResponse(
            authorizeURL: "https://x.com/i/oauth2/authorize?state=start-state",
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
            _ = try await service.connectViaOAuth(twitterUsername: nil)
            XCTFail("Expected missing callback state failure")
        } catch let error as XIntegrationError {
            XCTAssertEqual(error.localizedDescription, "OAuth callback missing state")
        }
    }

    @MainActor
    func testConnectViaOAuthThrowsWhenAuthorizeURLIsInvalid() async throws {
        let client = MockXIntegrationAPIClient()
        client.startResponse = XOAuthStartResponse(
            authorizeURL: "https://[::1",
            state: "start-state",
            scopes: ["tweet.read", "users.read"]
        )
        let service = XIntegrationService(client: client)

        do {
            _ = try await service.connectViaOAuth(twitterUsername: nil)
            XCTFail("Expected invalid authorize URL failure")
        } catch let error as XIntegrationError {
            XCTAssertEqual(error.localizedDescription, "Invalid OAuth authorize URL")
        }
    }
}

private final class MockXIntegrationAPIClient: XIntegrationAPIClientProtocol {
    var startResponse: XOAuthStartResponse?
    var exchangeResponse: XConnectionResponse?
    private(set) var recordedStartUsernames: [String?] = []
    private(set) var recordedExchangeCode: String?
    private(set) var recordedExchangeState: String?

    func fetchConnection() async throws -> XConnectionResponse {
        throw XIntegrationError.callbackParsingFailed
    }

    func startOAuth(twitterUsername: String?) async throws -> XOAuthStartResponse {
        recordedStartUsernames.append(twitterUsername)
        return try XCTUnwrap(startResponse)
    }

    func exchangeOAuth(code: String, state: String) async throws -> XConnectionResponse {
        recordedExchangeCode = code
        recordedExchangeState = state
        return try XCTUnwrap(exchangeResponse)
    }

    func disconnect() async throws {}
}
