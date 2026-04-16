import Foundation
import XCTest
@testable import newsly

final class APIClientAuthTests: XCTestCase {
    override func setUp() {
        super.setUp()
        MockURLProtocol.reset()
    }

    override func tearDown() {
        MockURLProtocol.reset()
        super.tearDown()
    }

    func testRequestUsesRefreshedTokenWhenAccessTokenMissing() async throws {
        let session = makeSession()
        let tokenStore = MockTokenStore(
            accessToken: nil,
            refreshToken: "refresh-token"
        )
        let refresher = MockTokenRefresher(
            tokenStore: tokenStore,
            result: .success("fresh-access-token")
        )
        let client = APIClient(
            session: session,
            tokenStore: tokenStore,
            tokenRefresher: refresher
        )

        MockURLProtocol.requestHandler = { request in
            XCTAssertEqual(
                request.value(forHTTPHeaderField: "Authorization"),
                "Bearer fresh-access-token"
            )

            return (
                HTTPURLResponse(
                    url: try XCTUnwrap(request.url),
                    statusCode: 200,
                    httpVersion: nil,
                    headerFields: nil
                )!,
                Data()
            )
        }

        try await client.requestVoid("/protected", method: "POST", body: Data("{}".utf8))

        XCTAssertEqual(refresher.refreshCallCount, 1)
        XCTAssertEqual(tokenStore.getToken(key: .accessToken), "fresh-access-token")
    }

    func testRequestRetriesAfterUnauthorizedUsingRefreshedToken() async throws {
        let session = makeSession()
        let tokenStore = MockTokenStore(
            accessToken: "stale-access-token",
            refreshToken: "refresh-token"
        )
        let refresher = MockTokenRefresher(
            tokenStore: tokenStore,
            result: .success("fresh-access-token")
        )
        let client = APIClient(
            session: session,
            tokenStore: tokenStore,
            tokenRefresher: refresher
        )
        var seenHeaders: [String?] = []

        MockURLProtocol.requestHandler = { request in
            seenHeaders.append(request.value(forHTTPHeaderField: "Authorization"))

            if seenHeaders.count == 1 {
                return (
                    HTTPURLResponse(
                        url: try XCTUnwrap(request.url),
                        statusCode: 401,
                        httpVersion: nil,
                        headerFields: ["Content-Type": "application/json"]
                    )!,
                    Data(#"{"detail":"token expired"}"#.utf8)
                )
            }

            return (
                HTTPURLResponse(
                    url: try XCTUnwrap(request.url),
                    statusCode: 200,
                    httpVersion: nil,
                    headerFields: nil
                )!,
                Data()
            )
        }

        try await client.requestVoid("/protected", method: "POST", body: Data("{}".utf8))

        XCTAssertEqual(seenHeaders, ["Bearer stale-access-token", "Bearer fresh-access-token"])
        XCTAssertEqual(refresher.refreshCallCount, 1)
    }

    func testRequestThrowsUnauthorizedWhenRefreshUnavailable() async {
        let logoutExpectation = expectation(description: "terminal refresh failure posts logout notification")
        logoutExpectation.assertForOverFulfill = false
        let observer = NotificationCenter.default.addObserver(
            forName: .authDidLogOut,
            object: nil,
            queue: nil
        ) { _ in
            logoutExpectation.fulfill()
        }
        defer {
            NotificationCenter.default.removeObserver(observer)
        }

        let session = makeSession()
        let tokenStore = MockTokenStore(
            accessToken: nil,
            refreshToken: nil
        )
        let refresher = MockTokenRefresher(
            tokenStore: tokenStore,
            result: .failure(AuthError.noRefreshToken)
        )
        let client = APIClient(
            session: session,
            tokenStore: tokenStore,
            tokenRefresher: refresher
        )

        MockURLProtocol.requestHandler = { request in
            XCTAssertNil(request.value(forHTTPHeaderField: "Authorization"))

            return (
                HTTPURLResponse(
                    url: try XCTUnwrap(request.url),
                    statusCode: 401,
                    httpVersion: nil,
                    headerFields: ["Content-Type": "application/json"]
                )!,
                Data(#"{"detail":"not authenticated"}"#.utf8)
            )
        }

        do {
            try await client.requestVoid("/protected", method: "GET")
            XCTFail("Expected unauthorized error")
        } catch let error as APIError {
            guard case .unauthorized = error else {
                return XCTFail("Unexpected APIError: \(error)")
            }
        } catch {
            XCTFail("Unexpected error: \(error)")
        }

        XCTAssertEqual(refresher.refreshCallCount, 1)
        await fulfillment(of: [logoutExpectation], timeout: 1)
    }

    func testTokenRefreshServicePersistsRotatedTokens() async throws {
        let session = makeSession()
        let tokenStore = MockTokenStore(
            accessToken: "old-access",
            refreshToken: "old-refresh"
        )
        let service = TokenRefreshService(
            session: session,
            tokenStore: tokenStore
        )

        MockURLProtocol.requestHandler = { request in
            XCTAssertEqual(request.httpMethod, "POST")
            XCTAssertEqual(request.url?.path, "/auth/refresh")

            return (
                HTTPURLResponse(
                    url: try XCTUnwrap(request.url),
                    statusCode: 200,
                    httpVersion: nil,
                    headerFields: ["Content-Type": "application/json"]
                )!,
                Data(#"{"access_token":"new-access","refresh_token":"new-refresh"}"#.utf8)
            )
        }

        let refreshed = try await service.refreshAccessToken()

        XCTAssertEqual(refreshed, "new-access")
        XCTAssertEqual(tokenStore.getToken(key: .accessToken), "new-access")
        XCTAssertEqual(tokenStore.getToken(key: .refreshToken), "new-refresh")
    }

    func testTokenRefreshServiceClearsTokensWhenRefreshExpires() async {
        let session = makeSession()
        let tokenStore = MockTokenStore(
            accessToken: "old-access",
            refreshToken: "old-refresh"
        )
        let service = TokenRefreshService(
            session: session,
            tokenStore: tokenStore
        )

        MockURLProtocol.requestHandler = { request in
            (
                HTTPURLResponse(
                    url: try XCTUnwrap(request.url),
                    statusCode: 401,
                    httpVersion: nil,
                    headerFields: ["Content-Type": "application/json"]
                )!,
                Data(#"{"detail":"invalid refresh token"}"#.utf8)
            )
        }

        do {
            _ = try await service.refreshAccessToken()
            XCTFail("Expected refreshTokenExpired error")
        } catch let error as AuthError {
            guard case .refreshTokenExpired = error else {
                return XCTFail("Unexpected AuthError: \(error)")
            }
        } catch {
            XCTFail("Unexpected error: \(error)")
        }

        XCTAssertNil(tokenStore.getToken(key: .accessToken))
        XCTAssertNil(tokenStore.getToken(key: .refreshToken))
    }

    func testServerAuthErrorUsesFriendlyMessageForHTMLGatewayResponse() {
        let html = """
        <!DOCTYPE html>
        <html>
        <head><title>willemsavenue.com | 502: Bad gateway</title></head>
        <body>Bad gateway</body>
        </html>
        """

        let error = AuthError.serverError(statusCode: 502, message: html)

        XCTAssertEqual(
            error.userFacingMessage,
            "Newsbuddy is temporarily unavailable. Please try again in a moment."
        )
    }

    func testServerAuthErrorExtractsJSONDetailMessage() {
        let error = AuthError.serverError(
            statusCode: 422,
            message: #"{"detail":"Sign in is not available for this account."}"#
        )

        XCTAssertEqual(error.userFacingMessage, "Sign in is not available for this account.")
    }

    private func makeSession() -> URLSession {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [MockURLProtocol.self]
        return URLSession(configuration: configuration)
    }
}

private final class MockTokenStore: AuthTokenStore {
    private var storage: [KeychainManager.KeychainKey: String]

    init(accessToken: String?, refreshToken: String?) {
        var storage: [KeychainManager.KeychainKey: String] = [:]
        if let accessToken {
            storage[.accessToken] = accessToken
        }
        if let refreshToken {
            storage[.refreshToken] = refreshToken
        }
        self.storage = storage
    }

    func getToken(key: KeychainManager.KeychainKey) -> String? {
        storage[key]
    }

    func saveToken(_ token: String, key: KeychainManager.KeychainKey) {
        storage[key] = token
    }

    func deleteToken(key: KeychainManager.KeychainKey) {
        storage.removeValue(forKey: key)
    }

    func clearAll() {
        storage.removeAll()
    }
}

private final class MockTokenRefresher: TokenRefreshing {
    private let tokenStore: MockTokenStore
    private let result: Result<String, Error>
    private(set) var refreshCallCount = 0

    init(tokenStore: MockTokenStore, result: Result<String, Error>) {
        self.tokenStore = tokenStore
        self.result = result
    }

    func refreshAccessToken() async throws -> String {
        refreshCallCount += 1

        switch result {
        case .success(let token):
            tokenStore.saveToken(token, key: .accessToken)
            return token
        case .failure(let error):
            throw error
        }
    }
}

private final class MockURLProtocol: URLProtocol {
    static var requestHandler: ((URLRequest) throws -> (HTTPURLResponse, Data))?

    static func reset() {
        requestHandler = nil
    }

    override class func canInit(with request: URLRequest) -> Bool {
        true
    }

    override class func canonicalRequest(for request: URLRequest) -> URLRequest {
        request
    }

    override func startLoading() {
        guard let handler = Self.requestHandler else {
            XCTFail("Missing request handler")
            return
        }

        do {
            let (response, data) = try handler(request)
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }

    override func stopLoading() {}
}
