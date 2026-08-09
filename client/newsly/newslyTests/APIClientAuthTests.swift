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

    func testTokenRefreshServiceDoesNotDeleteCredentialsWhenRefreshExpires() async {
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

        XCTAssertEqual(tokenStore.getToken(key: .accessToken), "old-access")
        XCTAssertEqual(tokenStore.getToken(key: .refreshToken), "old-refresh")
    }

    func testRejectedStaleRefreshPreservesTokensRotatedByAnotherProcess() async throws {
        let session = makeSession()
        let tokenStore = MockTokenStore(
            accessToken: "old-access",
            refreshToken: "old-refresh"
        )
        let service = TokenRefreshService(session: session, tokenStore: tokenStore)

        MockURLProtocol.requestHandler = { request in
            XCTAssertEqual(
                Self.refreshToken(in: request),
                "old-refresh",
                "The losing request must have captured the old one-time token first"
            )
            tokenStore.saveToken("winner-refresh", key: .refreshToken)
            tokenStore.saveToken("winner-access", key: .accessToken)
            return (
                HTTPURLResponse(
                    url: try XCTUnwrap(request.url),
                    statusCode: 401,
                    httpVersion: nil,
                    headerFields: ["Content-Type": "application/json"]
                )!,
                Data(#"{"detail":"already rotated"}"#.utf8)
            )
        }

        let accessToken = try await service.refreshAccessToken()

        XCTAssertEqual(accessToken, "winner-access")
        XCTAssertEqual(tokenStore.getToken(key: .accessToken), "winner-access")
        XCTAssertEqual(tokenStore.getToken(key: .refreshToken), "winner-refresh")
    }

    func testProcessLockSerializesRejectedTokenCheckAndWinnerRotation() async throws {
        let lockFileURL = FileManager.default.temporaryDirectory
            .appendingPathComponent("newsly-auth-lock-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: lockFileURL) }
        let loserLock = AuthRefreshProcessLock(fileURLProvider: { lockFileURL })
        let winnerLock = AuthRefreshProcessLock(fileURLProvider: { lockFileURL })
        let tokenStore = MockTokenStore(
            accessToken: "old-access",
            refreshToken: "old-refresh"
        )
        let loserCheckedToken = expectation(description: "loser checked rejected token")
        let winnerStarted = expectation(description: "winner attempted rotation")
        let loserResumeGate = APIAuthAsyncGate()

        let loser = Task {
            try await loserLock.withLock {
                let attemptedToken = tokenStore.getToken(key: .refreshToken)
                XCTAssertEqual(attemptedToken, "old-refresh")
                loserCheckedToken.fulfill()
                await loserResumeGate.wait()
                if tokenStore.getToken(key: .refreshToken) == attemptedToken {
                    tokenStore.deleteToken(key: .refreshToken)
                    tokenStore.deleteToken(key: .accessToken)
                }
            }
        }
        await fulfillment(of: [loserCheckedToken], timeout: 1)

        let winner = Task {
            winnerStarted.fulfill()
            try await winnerLock.withLock {
                tokenStore.saveToken("winner-refresh", key: .refreshToken)
                tokenStore.saveToken("winner-access", key: .accessToken)
            }
        }
        await fulfillment(of: [winnerStarted], timeout: 1)
        try? await Task.sleep(nanoseconds: 50_000_000)
        XCTAssertEqual(tokenStore.getToken(key: .refreshToken), "old-refresh")

        await loserResumeGate.open()
        try await loser.value
        try await winner.value

        XCTAssertEqual(tokenStore.getToken(key: .refreshToken), "winner-refresh")
        XCTAssertEqual(tokenStore.getToken(key: .accessToken), "winner-access")
    }

    func testRejectedStaleRefreshRetriesWhenOnlyRefreshTokenHasRotated() async throws {
        let session = makeSession()
        let tokenStore = MockTokenStore(
            accessToken: "old-access",
            refreshToken: "old-refresh"
        )
        let service = TokenRefreshService(session: session, tokenStore: tokenStore)
        let requestCounter = LockedCounter()

        MockURLProtocol.requestHandler = { request in
            requestCounter.increment()
            if requestCounter.value == 1 {
                tokenStore.saveToken("winner-refresh", key: .refreshToken)
                return (
                    HTTPURLResponse(
                        url: try XCTUnwrap(request.url),
                        statusCode: 401,
                        httpVersion: nil,
                        headerFields: ["Content-Type": "application/json"]
                    )!,
                    Data(#"{"detail":"already rotated"}"#.utf8)
                )
            }
            return (
                HTTPURLResponse(
                    url: try XCTUnwrap(request.url),
                    statusCode: 200,
                    httpVersion: nil,
                    headerFields: ["Content-Type": "application/json"]
                )!,
                Data(#"{"access_token":"final-access","refresh_token":"final-refresh"}"#.utf8)
            )
        }

        let accessToken = try await service.refreshAccessToken()

        XCTAssertEqual(accessToken, "final-access")
        XCTAssertEqual(requestCounter.value, 2)
        XCTAssertEqual(tokenStore.getToken(key: .refreshToken), "final-refresh")
    }

    func testConcurrentTokenRefreshCallsShareOneRequest() async throws {
        let session = makeSession()
        let tokenStore = MockTokenStore(
            accessToken: "old-access",
            refreshToken: "old-refresh"
        )
        let service = TokenRefreshService(
            session: session,
            tokenStore: tokenStore
        )
        let requestStarted = expectation(description: "refresh request started")
        requestStarted.assertForOverFulfill = false
        let responseGate = DispatchSemaphore(value: 0)
        let requestCounter = LockedCounter()

        MockURLProtocol.requestHandler = { request in
            requestCounter.increment()
            requestStarted.fulfill()
            _ = responseGate.wait(timeout: .now() + 2)
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

        let refreshTasks = (0..<12).map { _ in
            Task { try await service.refreshAccessToken() }
        }
        await fulfillment(of: [requestStarted], timeout: 1)
        try? await Task.sleep(nanoseconds: 50_000_000)

        XCTAssertEqual(requestCounter.value, 1)
        responseGate.signal()
        let tokens = try await refreshTasks.asyncValues()

        XCTAssertEqual(Set(tokens), ["new-access"])
        XCTAssertEqual(requestCounter.value, 1)
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

    private static func refreshToken(in request: URLRequest) -> String? {
        guard let body = bodyData(from: request),
              let json = try? JSONSerialization.jsonObject(with: body) as? [String: String] else {
            return nil
        }
        return json["refresh_token"]
    }

    private static func bodyData(from request: URLRequest) -> Data? {
        if let body = request.httpBody {
            return body
        }
        guard let stream = request.httpBodyStream else { return nil }

        stream.open()
        defer { stream.close() }
        var data = Data()
        let bufferSize = 1_024
        let buffer = UnsafeMutablePointer<UInt8>.allocate(capacity: bufferSize)
        defer { buffer.deallocate() }

        while true {
            let count = stream.read(buffer, maxLength: bufferSize)
            if count <= 0 { break }
            data.append(buffer, count: count)
        }
        return data
    }
}

private actor APIAuthAsyncGate {
    private var isOpen = false
    private var waiters: [CheckedContinuation<Void, Never>] = []

    func wait() async {
        guard !isOpen else { return }
        await withCheckedContinuation { continuation in
            waiters.append(continuation)
        }
    }

    func open() {
        guard !isOpen else { return }
        isOpen = true
        let pendingWaiters = waiters
        waiters.removeAll()
        for waiter in pendingWaiters {
            waiter.resume()
        }
    }
}

private final class MockTokenStore: AuthTokenStore {
    private let lock = NSLock()
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
        lock.withLock { storage[key] }
    }

    func saveToken(_ token: String, key: KeychainManager.KeychainKey) {
        lock.withLock { storage[key] = token }
    }

    func deleteToken(key: KeychainManager.KeychainKey) {
        lock.withLock { _ = storage.removeValue(forKey: key) }
    }

    func clearAll() {
        lock.withLock { storage.removeAll() }
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

    var hasStoredCredentialMaterial: Bool {
        let accessToken = tokenStore.getToken(key: .accessToken)
        let refreshToken = tokenStore.getToken(key: .refreshToken)
        return !(accessToken?.isEmpty ?? true) || !(refreshToken?.isEmpty ?? true)
    }

    func accessToken() async throws -> String {
        if let token = tokenStore.getToken(key: .accessToken) {
            return token
        }

        return try await refreshAccessToken()
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

private final class LockedCounter: @unchecked Sendable {
    private let lock = NSLock()
    private var count = 0

    var value: Int {
        lock.withLock { count }
    }

    func increment() {
        lock.withLock { count += 1 }
    }
}

private extension Array where Element == Task<String, Error> {
    func asyncValues() async throws -> [String] {
        var values: [String] = []
        values.reserveCapacity(count)
        for task in self {
            values.append(try await task.value)
        }
        return values
    }
}
