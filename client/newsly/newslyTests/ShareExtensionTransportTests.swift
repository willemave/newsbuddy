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
    private var accessToken: String?
    private var refreshToken: String?
    private var userId: String?

    init(accessToken: String? = nil, refreshToken: String? = nil) {
        self.accessToken = accessToken
        self.refreshToken = refreshToken
    }

    func getToken(key: KeychainManager.KeychainKey) -> String? {
        switch key {
        case .accessToken: accessToken
        case .refreshToken: refreshToken
        case .userId: userId
        }
    }

    func saveToken(_ token: String, key: KeychainManager.KeychainKey) {
        switch key {
        case .accessToken: accessToken = token
        case .refreshToken: refreshToken = token
        case .userId: userId = token
        }
    }

    func deleteToken(key: KeychainManager.KeychainKey) {
        switch key {
        case .accessToken: accessToken = nil
        case .refreshToken: refreshToken = nil
        case .userId: userId = nil
        }
    }

    func clearAll() {
        accessToken = nil
        refreshToken = nil
        userId = nil
    }
}

private final class ShareTransportURLProtocol: URLProtocol {
    static var requestHandler: ((URLRequest) throws -> (HTTPURLResponse, Data))?
    private(set) static var requestCount = 0

    static func reset() {
        requestHandler = nil
        requestCount = 0
    }

    override class func canInit(with request: URLRequest) -> Bool {
        true
    }

    override class func canonicalRequest(for request: URLRequest) -> URLRequest {
        request
    }

    override func startLoading() {
        Self.requestCount += 1
        guard let handler = Self.requestHandler else {
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
