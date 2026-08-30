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
        let refresher = MockCredentialSession(
            tokenStore: tokenStore,
            result: .success("fresh-access-token")
        )
        let client = APIClient(
            session: session,
            credentialSession: refresher
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

        try await client.requestVoid("/protected", method: .post, body: Data("{}".utf8))

        XCTAssertEqual(refresher.refreshCallCount, 1)
        XCTAssertEqual(tokenStore.getToken(key: .accessToken), "fresh-access-token")
    }

    func testRequestRetriesAfterUnauthorizedUsingRefreshedToken() async throws {
        let session = makeSession()
        let tokenStore = MockTokenStore(
            accessToken: "stale-access-token",
            refreshToken: "refresh-token"
        )
        let refresher = MockCredentialSession(
            tokenStore: tokenStore,
            result: .success("fresh-access-token")
        )
        let client = APIClient(
            session: session,
            credentialSession: refresher
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

        try await client.requestVoid("/protected", method: .post, body: Data("{}".utf8))

        XCTAssertEqual(seenHeaders, ["Bearer stale-access-token", "Bearer fresh-access-token"])
        XCTAssertEqual(refresher.refreshCallCount, 1)
    }

    func testOptInSafeReadRetriesConnectivityFailureAndSucceeds() async throws {
        let session = makeSession()
        let tokenStore = MockTokenStore(accessToken: nil, refreshToken: nil)
        let refresher = MockCredentialSession(
            tokenStore: tokenStore,
            result: .failure(ClientFailure.authenticationRequired)
        )
        let client = APIClient(
            session: session,
            credentialSession: refresher
        )
        let requestCounter = LockedCounter()

        MockURLProtocol.requestHandler = { request in
            requestCounter.increment()
            if requestCounter.value == 1 {
                throw URLError(.networkConnectionLost)
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

        try await client.requestVoid(
            "/briefing",
            method: .get,
            recoveryPolicy: RequestRecoveryPolicy(
                connectivityRetryDelaysNanoseconds: [0]
            ),
            authentication: .none
        )

        XCTAssertEqual(requestCounter.value, 2)
        XCTAssertEqual(refresher.refreshCallCount, 0)
    }

    func testSafeReadPolicyDefaultsOff() async {
        let session = makeSession()
        let tokenStore = MockTokenStore(accessToken: nil, refreshToken: nil)
        let refresher = MockCredentialSession(
            tokenStore: tokenStore,
            result: .failure(ClientFailure.authenticationRequired)
        )
        let client = APIClient(
            session: session,
            credentialSession: refresher
        )
        let requestCounter = LockedCounter()
        MockURLProtocol.requestHandler = { _ in
            requestCounter.increment()
            throw URLError(.notConnectedToInternet)
        }

        do {
            try await client.requestVoid(
                "/unmigrated",
                method: .get,
                authentication: .none
            )
            XCTFail("Expected a network failure")
        } catch {
            XCTAssertEqual(
                ClientFailure.classify(error),
                .connectivity(.notConnectedToInternet)
            )
        }

        XCTAssertEqual(requestCounter.value, 1)
    }

    func testSafeReadPolicyNeverRetriesPost() async {
        let session = makeSession()
        let tokenStore = MockTokenStore(accessToken: nil, refreshToken: nil)
        let refresher = MockCredentialSession(
            tokenStore: tokenStore,
            result: .failure(ClientFailure.authenticationRequired)
        )
        let client = APIClient(
            session: session,
            credentialSession: refresher
        )
        let requestCounter = LockedCounter()
        MockURLProtocol.requestHandler = { _ in
            requestCounter.increment()
            throw URLError(.networkConnectionLost)
        }

        do {
            try await client.requestVoid(
                "/command",
                method: .post,
                recoveryPolicy: RequestRecoveryPolicy(
                    connectivityRetryDelaysNanoseconds: [0, 0]
                ),
                authentication: .none
            )
            XCTFail("Expected a network failure")
        } catch {
            XCTAssertEqual(
                ClientFailure.classify(error),
                .connectivity(.networkConnectionLost)
            )
        }

        XCTAssertEqual(requestCounter.value, 1)
    }

    func testSafeReadDoesNotRetryAmbiguousRefreshFailure() async {
        let session = makeSession()
        let tokenStore = MockTokenStore(
            accessToken: "stale-access-token",
            refreshToken: "refresh-token"
        )
        let refresher = MockCredentialSession(
            tokenStore: tokenStore,
            result: .failure(ClientFailure.connectivity(.networkConnectionLost))
        )
        let client = APIClient(
            session: session,
            credentialSession: refresher
        )
        let requestCounter = LockedCounter()
        MockURLProtocol.requestHandler = { request in
            requestCounter.increment()
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

        do {
            try await client.requestVoid(
                "/protected",
                method: .get,
                recoveryPolicy: RequestRecoveryPolicy(
                    connectivityRetryDelaysNanoseconds: [0, 0]
                )
            )
            XCTFail("Expected refresh connectivity failure")
        } catch {
            XCTAssertEqual(
                ClientFailure.classify(error),
                .connectivity(.networkConnectionLost)
            )
        }

        XCTAssertEqual(requestCounter.value, 1)
        XCTAssertEqual(refresher.refreshCallCount, 1)
    }

    func testPermission403DoesNotRefreshCredentials() async {
        let session = makeSession()
        let tokenStore = MockTokenStore(
            accessToken: "access-token",
            refreshToken: "refresh-token"
        )
        let refresher = MockCredentialSession(
            tokenStore: tokenStore,
            result: .success("fresh-access-token")
        )
        let client = APIClient(
            session: session,
            credentialSession: refresher
        )
        MockURLProtocol.requestHandler = { request in
            (
                HTTPURLResponse(
                    url: try XCTUnwrap(request.url),
                    statusCode: 403,
                    httpVersion: nil,
                    headerFields: ["Content-Type": "application/json"]
                )!,
                Data(#"{"detail":"permission denied"}"#.utf8)
            )
        }

        do {
            try await client.requestVoid("/private-resource", method: .get)
            XCTFail("Expected permission failure")
        } catch let error as ClientFailure {
            guard case .http(let statusCode, _) = error else {
                return XCTFail("Unexpected ClientFailure: \(error)")
            }
            XCTAssertEqual(statusCode, 403)
        } catch {
            XCTFail("Unexpected error: \(error)")
        }

        XCTAssertEqual(refresher.refreshCallCount, 0)
    }

    func testConditionalReadPreservesHeadersAndAllowed304() async throws {
        let session = makeSession()
        let tokenStore = MockTokenStore(accessToken: nil, refreshToken: nil)
        let refresher = MockCredentialSession(
            tokenStore: tokenStore,
            result: .failure(ClientFailure.authenticationRequired)
        )
        let client = APIClient(
            session: session,
            credentialSession: refresher
        )
        MockURLProtocol.requestHandler = { request in
            XCTAssertEqual(request.value(forHTTPHeaderField: "If-None-Match"), "v1")
            return (
                HTTPURLResponse(
                    url: try XCTUnwrap(request.url),
                    statusCode: 304,
                    httpVersion: nil,
                    headerFields: ["ETag": "v1"]
                )!,
                Data()
            )
        }

        let (_, response) = try await client.requestHTTP(
            "/briefing",
            headers: ["If-None-Match": "v1"],
            allowedStatusCodes: [304],
            recoveryPolicy: .safeRead,
            authentication: .none
        )

        XCTAssertEqual(response.statusCode, 304)
        XCTAssertEqual(response.value(forHTTPHeaderField: "ETag"), "v1")
    }

    func testISO8601ResponsePolicyMatchesAuthenticationDates() async throws {
        let session = makeSession()
        let tokenStore = MockTokenStore(accessToken: nil, refreshToken: nil)
        let client = APIClient(
            session: session,
            credentialSession: MockCredentialSession(
                tokenStore: tokenStore,
                result: .failure(ClientFailure.authenticationRequired)
            )
        )
        MockURLProtocol.requestHandler = { request in
            (
                HTTPURLResponse(
                    url: try XCTUnwrap(request.url),
                    statusCode: 200,
                    httpVersion: nil,
                    headerFields: ["Content-Type": "application/json"]
                )!,
                Data(#"{"created_at":"2026-08-29T12:34:56Z"}"#.utf8)
            )
        }

        let response: APIISODateFixture = try await client.request(
            "/auth/date-fixture",
            authentication: .none,
            decoding: .iso8601
        )

        XCTAssertEqual(response.createdAt, ISO8601DateFormatter().date(from: "2026-08-29T12:34:56Z"))
    }

    func testDecodingFailureEmitsClientFailureWithEndpoint() async {
        let session = makeSession()
        let tokenStore = MockTokenStore(accessToken: nil, refreshToken: nil)
        let client = APIClient(
            session: session,
            credentialSession: MockCredentialSession(
                tokenStore: tokenStore,
                result: .failure(ClientFailure.authenticationRequired)
            )
        )
        MockURLProtocol.requestHandler = { request in
            (
                HTTPURLResponse(
                    url: try XCTUnwrap(request.url),
                    statusCode: 200,
                    httpVersion: nil,
                    headerFields: ["Content-Type": "application/json"]
                )!,
                Data(#"{"created_at":42}"#.utf8)
            )
        }

        do {
            let _: APIISODateFixture = try await client.request(
                "/auth/date-fixture",
                authentication: .none,
                decoding: .iso8601
            )
            XCTFail("Expected decoding failure")
        } catch let failure as ClientFailure {
            XCTAssertEqual(failure, .decoding(endpoint: "/auth/date-fixture"))
        } catch {
            XCTFail("Unexpected error: \(error)")
        }
    }

    func testRequiredRequestWithoutCredentialsFailsLocally() async {
        let session = makeSession()
        let tokenStore = MockTokenStore(
            accessToken: nil,
            refreshToken: nil
        )
        let refresher = MockCredentialSession(
            tokenStore: tokenStore,
            result: .failure(ClientFailure.authenticationRequired)
        )
        let client = APIClient(
            session: session,
            credentialSession: refresher
        )

        let requestCount = LockedCounter()
        MockURLProtocol.requestHandler = { _ in
            requestCount.increment()
            throw URLError(.badServerResponse)
        }

        do {
            try await client.requestVoid("/protected", method: .get)
            XCTFail("Expected unauthorized error")
        } catch {
            XCTAssertEqual(ClientFailure.classify(error), .authenticationRequired)
        }

        XCTAssertEqual(refresher.refreshCallCount, 0)
        XCTAssertEqual(requestCount.value, 0)
    }

    func testCredentialSessionPersistsRotatedTokens() async throws {
        let session = makeSession()
        let tokenStore = MockTokenStore(
            accessToken: "old-access",
            refreshToken: "old-refresh"
        )
        let service = makeCredentialSession(
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
                Data(#"{"access_token":"new-access","refresh_token":"new-refresh","token_type":"bearer"}"#.utf8)
            )
        }

        let refreshed = try await service.refreshAfterRejection(rejectedAccessToken: nil)

        XCTAssertEqual(refreshed, "new-access")
        XCTAssertEqual(tokenStore.getToken(key: .accessToken), "new-access")
        XCTAssertEqual(tokenStore.getToken(key: .refreshToken), "new-refresh")
        XCTAssertNil(tokenStore.getToken(key: .refreshAttempt))
    }

    func testRefreshRetriesAmbiguousResponseLossWithSameAttemptID() async throws {
        let session = makeSession()
        let tokenStore = MockTokenStore(
            accessToken: "old-access",
            refreshToken: "old-refresh"
        )
        let service = makeCredentialSession(
            session: session,
            tokenStore: tokenStore,
            retryDelaysNanoseconds: [0]
        )
        let requestCounter = LockedCounter()
        let attemptIDs = LockedValues<String>()

        MockURLProtocol.requestHandler = { request in
            requestCounter.increment()
            attemptIDs.append(try XCTUnwrap(Self.refreshAttemptID(in: request)))
            if requestCounter.value == 1 {
                throw URLError(.networkConnectionLost)
            }
            return (
                HTTPURLResponse(
                    url: try XCTUnwrap(request.url),
                    statusCode: 200,
                    httpVersion: nil,
                    headerFields: ["Content-Type": "application/json"]
                )!,
                Data(#"{"access_token":"new-access","refresh_token":"new-refresh","token_type":"bearer"}"#.utf8)
            )
        }

        let accessToken = try await service.refreshAfterRejection(rejectedAccessToken: nil)

        XCTAssertEqual(accessToken, "new-access")
        XCTAssertEqual(requestCounter.value, 2)
        XCTAssertEqual(Set(attemptIDs.values).count, 1)
    }

    func testRefreshAttemptSurvivesProcessRecreationAfterAmbiguousFailure() async throws {
        let session = makeSession()
        let tokenStore = MockTokenStore(
            accessToken: "old-access",
            refreshToken: "old-refresh"
        )
        let firstService = makeCredentialSession(
            session: session,
            tokenStore: tokenStore,
            retryDelaysNanoseconds: []
        )
        let attemptIDs = LockedValues<String>()

        MockURLProtocol.requestHandler = { request in
            attemptIDs.append(try XCTUnwrap(Self.refreshAttemptID(in: request)))
            throw URLError(.networkConnectionLost)
        }
        do {
            _ = try await firstService.refreshAfterRejection(rejectedAccessToken: nil)
            XCTFail("Expected ambiguous refresh failure")
        } catch {
            XCTAssertEqual(
                ClientFailure.classify(error),
                .connectivity(.networkConnectionLost)
            )
        }

        let secondService = makeCredentialSession(
            session: session,
            tokenStore: tokenStore,
            retryDelaysNanoseconds: []
        )
        MockURLProtocol.requestHandler = { request in
            attemptIDs.append(try XCTUnwrap(Self.refreshAttemptID(in: request)))
            return (
                HTTPURLResponse(
                    url: try XCTUnwrap(request.url),
                    statusCode: 200,
                    httpVersion: nil,
                    headerFields: ["Content-Type": "application/json"]
                )!,
                Data(#"{"access_token":"new-access","refresh_token":"new-refresh","token_type":"bearer"}"#.utf8)
            )
        }

        _ = try await secondService.refreshAfterRejection(rejectedAccessToken: nil)

        XCTAssertEqual(attemptIDs.values.count, 2)
        XCTAssertEqual(Set(attemptIDs.values).count, 1)
    }

    func testCredentialSessionDoesNotDeleteCredentialsWhenRefreshExpires() async {
        let session = makeSession()
        let tokenStore = MockTokenStore(
            accessToken: "old-access",
            refreshToken: "old-refresh"
        )
        let service = makeCredentialSession(
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
            _ = try await service.refreshAfterRejection(rejectedAccessToken: nil)
            XCTFail("Expected refreshTokenExpired error")
        } catch {
            XCTAssertEqual(ClientFailure.classify(error), .authenticationExpired)
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
        let service = makeCredentialSession(session: session, tokenStore: tokenStore)

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

        let accessToken = try await service.refreshAfterRejection(rejectedAccessToken: nil)

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
        let service = makeCredentialSession(session: session, tokenStore: tokenStore)
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
                Data(#"{"access_token":"final-access","refresh_token":"final-refresh","token_type":"bearer"}"#.utf8)
            )
        }

        let accessToken = try await service.refreshAfterRejection(rejectedAccessToken: nil)

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
        let service = makeCredentialSession(
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
                Data(#"{"access_token":"new-access","refresh_token":"new-refresh","token_type":"bearer"}"#.utf8)
            )
        }

        let refreshTasks = (0..<12).map { _ in
            Task { try await service.refreshAfterRejection(rejectedAccessToken: nil) }
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

    private func makeCredentialSession(
        session: URLSession,
        tokenStore: MockTokenStore,
        retryDelaysNanoseconds: [UInt64] = [250_000_000, 750_000_000]
    ) -> CredentialSession {
        let lockURL = FileManager.default.temporaryDirectory.appendingPathComponent(
            "api-client-auth-\(UUID().uuidString).lock"
        )
        return CredentialSession(
            storage: TokenStoreCredentialStorage(tokenStore: tokenStore),
            exchange: RefreshTokenExchange(
                transport: HTTPTransport(session: session),
                baseURLProvider: { URL(string: "https://api.example.com") },
                retryDelaysNanoseconds: retryDelaysNanoseconds
            ),
            processLock: AuthRefreshProcessLock(fileURLProvider: { lockURL }),
            attemptStore: KeychainRefreshAttemptStore(persistence: tokenStore),
            cooldownSeconds: 0
        )
    }

    private static func refreshToken(in request: URLRequest) -> String? {
        guard let body = bodyData(from: request),
              let json = try? JSONSerialization.jsonObject(with: body) as? [String: String] else {
            return nil
        }
        return json["refresh_token"]
    }

    private static func refreshAttemptID(in request: URLRequest) -> String? {
        guard let body = bodyData(from: request),
              let json = try? JSONSerialization.jsonObject(with: body) as? [String: Any] else {
            return nil
        }
        return json["attempt_id"] as? String
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

private struct APIISODateFixture: Decodable {
    let createdAt: Date

    enum CodingKeys: String, CodingKey {
        case createdAt = "created_at"
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

private final class MockTokenStore: AuthTokenStore, RefreshAttemptPersisting {
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

    func readRefreshAttempt() -> RefreshAttemptPersistenceRead {
        lock.withLock {
            storage[.refreshAttempt].map(RefreshAttemptPersistenceRead.value) ?? .missing
        }
    }

    func persistRefreshAttempt(_ encodedEnvelope: String) throws {
        lock.withLock { storage[.refreshAttempt] = encodedEnvelope }
    }

    func deleteRefreshAttempt() {
        lock.withLock { _ = storage.removeValue(forKey: .refreshAttempt) }
    }
}

private final class MockCredentialSession: CredentialSessionProviding {
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

    func accessToken(for authentication: RequestAuthentication) async throws -> String? {
        guard authentication != .none else { return nil }
        if let token = tokenStore.getToken(key: .accessToken) {
            return token
        }
        guard tokenStore.getToken(key: .refreshToken)?.isEmpty == false else {
            throw ClientFailure.authenticationRequired
        }
        return try await refreshAfterRejection(rejectedAccessToken: nil)
    }

    func refreshAfterRejection(rejectedAccessToken: String?) async throws -> String {
        _ = rejectedAccessToken
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

private final class LockedValues<Value>: @unchecked Sendable {
    private let lock = NSLock()
    private var storage: [Value] = []

    var values: [Value] {
        lock.withLock { storage }
    }

    func append(_ value: Value) {
        lock.withLock { storage.append(value) }
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
