import Foundation
import XCTest
@testable import newsly

final class ShareExtensionTransportTests: XCTestCase {
    override func setUp() {
        super.setUp()
        ShareTransportURLProtocol.reset()
    }

    override func tearDown() {
        ShareTransportURLProtocol.reset()
        super.tearDown()
    }

    func testRequestUsesStoredAccessToken() async throws {
        let tokenStore = ShareTransportTokenStore(
            accessToken: "access-token",
            refreshToken: "refresh-token"
        )
        let transport = makeTransport(tokenStore: tokenStore)

        ShareTransportURLProtocol.requestHandler = { request in
            XCTAssertEqual(request.url?.path, "/api/share-actions")
            XCTAssertEqual(request.httpMethod, "POST")
            XCTAssertEqual(
                request.value(forHTTPHeaderField: "Authorization"),
                "Bearer access-token"
            )
            XCTAssertEqual(
                Self.bodyData(from: request),
                Data(#"{"url":"https://example.com"}"#.utf8)
            )
            return Self.response(for: request, statusCode: 204, data: Data())
        }

        try await transport.requestVoid(
            "/api/share-actions",
            body: Data(#"{"url":"https://example.com"}"#.utf8)
        )

        XCTAssertEqual(ShareTransportURLProtocol.requestCount, 1)
    }

    func testUnauthorizedRequestRefreshesAndRetriesOnce() async throws {
        let tokenStore = ShareTransportTokenStore(
            accessToken: "expired-token",
            refreshToken: "refresh-token"
        )
        let transport = makeTransport(tokenStore: tokenStore)
        var requestPaths: [String] = []

        ShareTransportURLProtocol.requestHandler = { request in
            requestPaths.append(request.url?.path ?? "")
            switch requestPaths.count {
            case 1:
                XCTAssertEqual(
                    request.value(forHTTPHeaderField: "Authorization"),
                    "Bearer expired-token"
                )
                return Self.response(
                    for: request,
                    statusCode: 401,
                    data: Data(#"{"detail":"token expired"}"#.utf8)
                )
            case 2:
                XCTAssertEqual(request.url?.path, "/auth/refresh")
                XCTAssertNil(request.value(forHTTPHeaderField: "Authorization"))
                return Self.response(
                    for: request,
                    statusCode: 200,
                    data: Data(
                        #"{"access_token":"fresh-access","refresh_token":"fresh-refresh"}"#.utf8
                    )
                )
            default:
                XCTAssertEqual(
                    request.value(forHTTPHeaderField: "Authorization"),
                    "Bearer fresh-access"
                )
                return Self.response(for: request, statusCode: 204, data: Data())
            }
        }

        try await transport.requestVoid("/api/share-actions", body: Data("{}".utf8))

        XCTAssertEqual(
            requestPaths,
            ["/api/share-actions", "/auth/refresh", "/api/share-actions"]
        )
        XCTAssertEqual(tokenStore.getToken(key: .accessToken), "fresh-access")
        XCTAssertEqual(tokenStore.getToken(key: .refreshToken), "fresh-refresh")
    }

    func testRejectedStaleRefreshUsesTokensRotatedByAnotherProcess() async throws {
        let tokenStore = ShareTransportTokenStore(
            accessToken: "old-access",
            refreshToken: "old-refresh"
        )
        let transport = makeTransport(tokenStore: tokenStore)
        var requestPaths: [String] = []

        ShareTransportURLProtocol.requestHandler = { request in
            requestPaths.append(request.url?.path ?? "")
            switch requestPaths.count {
            case 1:
                XCTAssertEqual(
                    request.value(forHTTPHeaderField: "Authorization"),
                    "Bearer old-access"
                )
                return Self.response(
                    for: request,
                    statusCode: 401,
                    data: Data(#"{"detail":"token expired"}"#.utf8)
                )
            case 2:
                XCTAssertEqual(request.url?.path, "/auth/refresh")
                tokenStore.saveToken("winner-refresh", key: .refreshToken)
                tokenStore.saveToken("winner-access", key: .accessToken)
                return Self.response(
                    for: request,
                    statusCode: 401,
                    data: Data(#"{"detail":"already rotated"}"#.utf8)
                )
            default:
                XCTAssertEqual(
                    request.value(forHTTPHeaderField: "Authorization"),
                    "Bearer winner-access"
                )
                return Self.response(for: request, statusCode: 204, data: Data())
            }
        }

        try await transport.requestVoid("/api/share-actions", body: Data("{}".utf8))

        XCTAssertEqual(
            requestPaths,
            ["/api/share-actions", "/auth/refresh", "/api/share-actions"]
        )
        XCTAssertEqual(tokenStore.getToken(key: .accessToken), "winner-access")
        XCTAssertEqual(tokenStore.getToken(key: .refreshToken), "winner-refresh")
    }

    func testConcurrentRequestsShareOneRefreshRequest() async throws {
        let tokenStore = ShareTransportTokenStore(
            accessToken: nil,
            refreshToken: "refresh-token"
        )
        let transport = makeTransport(tokenStore: tokenStore)
        let refreshStarted = expectation(description: "refresh request started")
        refreshStarted.assertForOverFulfill = false
        let refreshGate = DispatchSemaphore(value: 0)
        let refreshCounter = ShareLockedCounter()

        ShareTransportURLProtocol.requestHandler = { request in
            if request.url?.path == "/auth/refresh" {
                refreshCounter.increment()
                refreshStarted.fulfill()
                _ = refreshGate.wait(timeout: .now() + 2)
                return Self.response(
                    for: request,
                    statusCode: 200,
                    data: Data(
                        #"{"access_token":"fresh-access","refresh_token":"fresh-refresh"}"#.utf8
                    )
                )
            }

            XCTAssertEqual(
                request.value(forHTTPHeaderField: "Authorization"),
                "Bearer fresh-access"
            )
            return Self.response(for: request, statusCode: 204, data: Data())
        }

        async let first: Void = transport.requestVoid("/api/share-actions", body: Data("{}".utf8))
        async let second: Void = transport.requestVoid("/api/share-actions", body: Data("{}".utf8))

        await fulfillment(of: [refreshStarted], timeout: 1)
        try? await Task.sleep(nanoseconds: 50_000_000)
        XCTAssertEqual(refreshCounter.value, 1)
        refreshGate.signal()
        refreshGate.signal()
        _ = try await (first, second)

        XCTAssertEqual(refreshCounter.value, 1)
        XCTAssertEqual(tokenStore.getToken(key: .accessToken), "fresh-access")
        XCTAssertEqual(tokenStore.getToken(key: .refreshToken), "fresh-refresh")
    }

    func testPermissionFailureDoesNotRefresh() async {
        let tokenStore = ShareTransportTokenStore(
            accessToken: "access-token",
            refreshToken: "refresh-token"
        )
        let transport = makeTransport(tokenStore: tokenStore)

        ShareTransportURLProtocol.requestHandler = { request in
            Self.response(
                for: request,
                statusCode: 403,
                data: Data(#"{"detail":"sharing is disabled"}"#.utf8)
            )
        }

        do {
            try await transport.requestVoid("/api/share-actions")
            XCTFail("Expected permission failure")
        } catch ShareExtensionTransportError.server(let statusCode, let detail) {
            XCTAssertEqual(statusCode, 403)
            XCTAssertEqual(detail, "sharing is disabled")
        } catch {
            XCTFail("Unexpected error: \(error)")
        }

        XCTAssertEqual(ShareTransportURLProtocol.requestCount, 1)
    }

    func testMissingCredentialsDoesNotSendUnauthenticatedRequest() async {
        let transport = makeTransport(tokenStore: ShareTransportTokenStore())

        do {
            try await transport.requestVoid("/api/share-actions")
            XCTFail("Expected authentication failure")
        } catch ShareExtensionTransportError.notAuthenticated {
            // Expected.
        } catch {
            XCTFail("Unexpected error: \(error)")
        }

        XCTAssertEqual(ShareTransportURLProtocol.requestCount, 0)
    }

    func testSubmissionStateRejectsInvalidURLWithoutOfferingDeadEndRetry() {
        var state = ShareSubmissionPresentationState()

        XCTAssertFalse(state.begin(hasValidURL: false))
        XCTAssertEqual(state.phase, .failed(.invalidURL))
        XCTAssertTrue(state.canBeginSubmission)
        XCTAssertNil(state.recoveryAction)
        XCTAssertFalse(state.isSubmitting)
    }

    func testSubmissionStateImmediateFailureOffersRetry() {
        var state = ShareSubmissionPresentationState()

        XCTAssertTrue(state.begin(hasValidURL: true))
        state.fail(.recoverable)

        XCTAssertEqual(
            state.phase,
            .failed(.recoverable)
        )
        XCTAssertTrue(state.canBeginSubmission)
        XCTAssertEqual(state.recoveryAction, .retry)
    }

    func testSubmissionStateBlocksInFlightAndNonRetryablePhasesWithoutChangingThem() {
        var submitting = ShareSubmissionPresentationState()
        XCTAssertTrue(submitting.begin(hasValidURL: true))
        XCTAssertFalse(submitting.canBeginSubmission)
        XCTAssertFalse(submitting.begin(hasValidURL: false))
        XCTAssertEqual(submitting.phase, .submitting)

        var authenticationRequired = ShareSubmissionPresentationState()
        XCTAssertTrue(authenticationRequired.begin(hasValidURL: true))
        authenticationRequired.fail(.authenticationRequired)
        XCTAssertFalse(authenticationRequired.canBeginSubmission)
        XCTAssertFalse(authenticationRequired.begin(hasValidURL: false))
        XCTAssertEqual(authenticationRequired.phase, .failed(.authenticationRequired))

        authenticationRequired.finishOpeningApp(opened: false)
        XCTAssertFalse(authenticationRequired.canBeginSubmission)
        XCTAssertFalse(authenticationRequired.begin(hasValidURL: false))
        XCTAssertEqual(authenticationRequired.phase, .manualOpenFallback)

        authenticationRequired.finishManualFallback()
        XCTAssertFalse(authenticationRequired.canBeginSubmission)
        XCTAssertFalse(authenticationRequired.begin(hasValidURL: false))
        XCTAssertEqual(authenticationRequired.phase, .completed)
    }

    func testSubmissionStateRetryCanSucceed() {
        var state = ShareSubmissionPresentationState()
        XCTAssertTrue(state.begin(hasValidURL: true))
        state.fail(.recoverable)

        XCTAssertTrue(state.begin(hasValidURL: true))
        XCTAssertTrue(state.isSubmitting)
        state.succeed()

        XCTAssertEqual(state.phase, .completed)
        XCTAssertFalse(state.canBeginSubmission)
        XCTAssertNil(state.recoveryAction)
        XCTAssertFalse(state.begin(hasValidURL: true))
    }

    func testSubmissionStateRetryFailureKeepsRetryAvailable() {
        var state = ShareSubmissionPresentationState()
        XCTAssertTrue(state.begin(hasValidURL: true))
        state.fail(.recoverable)

        XCTAssertTrue(state.begin(hasValidURL: true))
        state.fail(.recoverable)

        XCTAssertEqual(state.phase, .failed(.recoverable))
        XCTAssertEqual(state.recoveryAction, .retry)
    }

    func testSubmissionStateOpenAppFailureOffersCopyAndCloseFallback() {
        var state = ShareSubmissionPresentationState()
        XCTAssertTrue(state.begin(hasValidURL: true))
        state.fail(.authenticationRequired)

        XCTAssertEqual(state.recoveryAction, .openApp)
        state.finishOpeningApp(opened: false)

        XCTAssertEqual(state.phase, .manualOpenFallback)
        XCTAssertNil(state.recoveryAction)

        state.finishManualFallback()
        XCTAssertEqual(state.phase, .completed)
        XCTAssertFalse(state.begin(hasValidURL: true))
    }

    func testSubmissionStateOpenAppSuccessIsTerminal() {
        var state = ShareSubmissionPresentationState()
        XCTAssertTrue(state.begin(hasValidURL: true))
        state.fail(.authenticationRequired)

        state.finishOpeningApp(opened: true)

        XCTAssertEqual(state.phase, .completed)
        XCTAssertNil(state.recoveryAction)
        XCTAssertFalse(state.begin(hasValidURL: true))
    }

    private func makeTransport(tokenStore: ShareTransportTokenStore) -> ShareExtensionTransport {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [ShareTransportURLProtocol.self]
        return ShareExtensionTransport(
            session: URLSession(configuration: configuration),
            tokenStore: tokenStore,
            baseURLProvider: { URL(string: "https://api.example.com") }
        )
    }

    private static func response(
        for request: URLRequest,
        statusCode: Int,
        data: Data
    ) -> (HTTPURLResponse, Data) {
        (
            HTTPURLResponse(
                url: request.url!,
                statusCode: statusCode,
                httpVersion: nil,
                headerFields: ["Content-Type": "application/json"]
            )!,
            data
        )
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
            if count <= 0 {
                break
            }
            data.append(buffer, count: count)
        }
        return data
    }
}

private final class ShareTransportTokenStore: AuthTokenStore {
    private let lock = NSLock()
    private var storage: [KeychainManager.KeychainKey: String]

    init(accessToken: String? = nil, refreshToken: String? = nil) {
        var storage: [KeychainManager.KeychainKey: String] = [:]
        storage[.accessToken] = accessToken
        storage[.refreshToken] = refreshToken
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

private final class ShareLockedCounter {
    private let lock = NSLock()
    private var count = 0

    var value: Int {
        lock.withLock { count }
    }

    func increment() {
        lock.withLock { count += 1 }
    }
}

private final class ShareTransportURLProtocol: URLProtocol {
    private static let lock = NSLock()
    private static var storedRequestHandler: ((URLRequest) throws -> (HTTPURLResponse, Data))?
    private static var storedRequestCount = 0

    static var requestHandler: ((URLRequest) throws -> (HTTPURLResponse, Data))? {
        get { lock.withLock { storedRequestHandler } }
        set { lock.withLock { storedRequestHandler = newValue } }
    }

    static var requestCount: Int {
        lock.withLock { storedRequestCount }
    }

    static func reset() {
        lock.withLock {
            storedRequestHandler = nil
            storedRequestCount = 0
        }
    }

    override class func canInit(with request: URLRequest) -> Bool {
        true
    }

    override class func canonicalRequest(for request: URLRequest) -> URLRequest {
        request
    }

    override func startLoading() {
        let handler = Self.lock.withLock {
            Self.storedRequestCount += 1
            return Self.storedRequestHandler
        }
        guard let handler else {
            XCTFail("Missing ShareTransportURLProtocol handler")
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
