import Foundation
import XCTest
@testable import newsly

final class OpenAIServiceTests: XCTestCase {
    override func setUp() {
        super.setUp()
        OpenAIServiceURLProtocol.reset()
    }

    override func tearDown() {
        OpenAIServiceURLProtocol.reset()
        super.tearDown()
    }

    func testTranscriptionUsesAuthenticatedMultipartRequest() async throws {
        let audioData = Data("test-audio-bytes".utf8)
        let service = makeService(
            accessToken: "access-token",
            refreshToken: "refresh-token"
        )

        OpenAIServiceURLProtocol.requestHandler = { request in
            XCTAssertEqual(request.httpMethod, "POST")
            XCTAssertEqual(request.url?.path, APIEndpoints.openaiTranscriptions)
            XCTAssertEqual(
                request.value(forHTTPHeaderField: "Authorization"),
                "Bearer access-token"
            )

            let contentType = try XCTUnwrap(
                request.value(forHTTPHeaderField: "Content-Type")
            )
            XCTAssertTrue(contentType.hasPrefix("multipart/form-data; boundary="))

            let body = try XCTUnwrap(Self.bodyData(from: request))
            XCTAssertNotNil(body.range(of: Data("filename=\"voice-note.m4a\"".utf8)))
            XCTAssertNotNil(body.range(of: Data("Content-Type: audio/m4a".utf8)))
            XCTAssertNotNil(body.range(of: audioData))

            return (
                HTTPURLResponse(
                    url: try XCTUnwrap(request.url),
                    statusCode: 200,
                    httpVersion: nil,
                    headerFields: ["Content-Type": "application/json"]
                )!,
                Data(#"{"transcript":"hello world","language":"en"}"#.utf8)
            )
        }

        let fileURL = try makeTemporaryAudioFile(data: audioData)
        defer { try? FileManager.default.removeItem(at: fileURL) }

        let response = try await service.transcribeAudio(
            fileURL: fileURL,
            filename: "voice-note.m4a"
        )

        XCTAssertEqual(response.text, "hello world")
        XCTAssertEqual(response.language, "en")
    }

    func testBearerRecoveryReplaysSameMultipartRequestWithRefreshedToken() async throws {
        let tokenStore = OpenAIServiceTokenStore(
            accessToken: "stale-access",
            refreshToken: "refresh-token"
        )
        let credentialSession = OpenAIServiceCredentialSession(
            tokenStore: tokenStore,
            result: .success("fresh-access")
        )
        let service = makeService(
            tokenStore: tokenStore,
            credentialSession: credentialSession
        )
        let requests = OpenAIServiceCapturedRequests()

        OpenAIServiceURLProtocol.requestHandler = { request in
            requests.append(
                authorization: request.value(forHTTPHeaderField: "Authorization"),
                contentType: request.value(forHTTPHeaderField: "Content-Type"),
                body: Self.bodyData(from: request)
            )

            if requests.count == 1 {
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
                    headerFields: ["Content-Type": "application/json"]
                )!,
                Data(#"{"text":"recovered"}"#.utf8)
            )
        }

        let fileURL = try makeTemporaryAudioFile(data: Data("audio".utf8))
        defer { try? FileManager.default.removeItem(at: fileURL) }

        let response = try await service.transcribeAudio(fileURL: fileURL)

        XCTAssertEqual(response.text, "recovered")
        XCTAssertEqual(credentialSession.refreshCallCount, 1)
        XCTAssertEqual(
            requests.authorizations,
            ["Bearer stale-access", "Bearer fresh-access"]
        )
        XCTAssertEqual(requests.contentTypes.count, 2)
        XCTAssertEqual(requests.contentTypes[0], requests.contentTypes[1])
        XCTAssertEqual(requests.bodies.count, 2)
        XCTAssertEqual(requests.bodies[0], requests.bodies[1])
    }

    func testMissingCredentialsMapsToNotAuthenticatedWithoutSendingRequest() async throws {
        let service = makeService(accessToken: nil, refreshToken: nil)
        OpenAIServiceURLProtocol.requestHandler = { _ in
            XCTFail("A request must not be sent without credentials")
            throw URLError(.badServerResponse)
        }

        let fileURL = try makeTemporaryAudioFile(data: Data("audio".utf8))
        defer { try? FileManager.default.removeItem(at: fileURL) }

        do {
            _ = try await service.transcribeAudio(fileURL: fileURL)
            XCTFail("Expected an authentication error")
        } catch let error as OpenAIServiceError {
            guard case .notAuthenticated = error else {
                return XCTFail("Unexpected OpenAIServiceError: \(error)")
            }
        } catch {
            XCTFail("Unexpected error: \(error)")
        }

        XCTAssertEqual(OpenAIServiceURLProtocol.requestCount, 0)
    }

    func testHTTPFailurePreservesServiceErrorMapping() async throws {
        let service = makeService(
            accessToken: "access-token",
            refreshToken: "refresh-token"
        )
        OpenAIServiceURLProtocol.requestHandler = { request in
            (
                HTTPURLResponse(
                    url: try XCTUnwrap(request.url),
                    statusCode: 503,
                    httpVersion: nil,
                    headerFields: ["Content-Type": "application/json"]
                )!,
                Data(#"{"detail":"temporarily unavailable"}"#.utf8)
            )
        }

        let fileURL = try makeTemporaryAudioFile(data: Data("audio".utf8))
        defer { try? FileManager.default.removeItem(at: fileURL) }

        do {
            _ = try await service.transcribeAudio(fileURL: fileURL)
            XCTFail("Expected a server error")
        } catch let error as OpenAIServiceError {
            guard case .serverError(let statusCode, let message) = error else {
                return XCTFail("Unexpected OpenAIServiceError: \(error)")
            }
            XCTAssertEqual(statusCode, 503)
            XCTAssertEqual(message, "temporarily unavailable")
        } catch {
            XCTFail("Unexpected error: \(error)")
        }
    }

    func testMalformedSuccessResponseMapsToInvalidResponse() async throws {
        let service = makeService(
            accessToken: "access-token",
            refreshToken: "refresh-token"
        )
        OpenAIServiceURLProtocol.requestHandler = { request in
            (
                HTTPURLResponse(
                    url: try XCTUnwrap(request.url),
                    statusCode: 200,
                    httpVersion: nil,
                    headerFields: ["Content-Type": "application/json"]
                )!,
                Data("not-json".utf8)
            )
        }

        let fileURL = try makeTemporaryAudioFile(data: Data("audio".utf8))
        defer { try? FileManager.default.removeItem(at: fileURL) }

        do {
            _ = try await service.transcribeAudio(fileURL: fileURL)
            XCTFail("Expected an invalid response error")
        } catch let error as OpenAIServiceError {
            guard case .invalidResponse = error else {
                return XCTFail("Unexpected OpenAIServiceError: \(error)")
            }
        } catch {
            XCTFail("Unexpected error: \(error)")
        }
    }

    private func makeService(
        accessToken: String?,
        refreshToken: String?
    ) -> OpenAIService {
        let tokenStore = OpenAIServiceTokenStore(
            accessToken: accessToken,
            refreshToken: refreshToken
        )
        let credentialSession = OpenAIServiceCredentialSession(
            tokenStore: tokenStore,
            result: .failure(ClientFailure.authenticationRequired)
        )
        return makeService(
            tokenStore: tokenStore,
            credentialSession: credentialSession
        )
    }

    private func makeService(
        tokenStore: OpenAIServiceTokenStore,
        credentialSession: OpenAIServiceCredentialSession
    ) -> OpenAIService {
        let apiClient = APIClient(
            session: makeSession(),
            credentialSession: credentialSession
        )
        return OpenAIService(
            apiClient: apiClient,
            credentialSession: credentialSession
        )
    }

    private func makeSession() -> URLSession {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [OpenAIServiceURLProtocol.self]
        return URLSession(configuration: configuration)
    }

    private func makeTemporaryAudioFile(data: Data) throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("openai-service-\(UUID().uuidString).m4a")
        try data.write(to: url)
        return url
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

private final class OpenAIServiceTokenStore: AuthTokenStore {
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

private final class OpenAIServiceCredentialSession: CredentialSessionProviding {
    private let lock = NSLock()
    private let tokenStore: OpenAIServiceTokenStore
    private let result: Result<String, Error>
    private var refreshCalls = 0

    init(
        tokenStore: OpenAIServiceTokenStore,
        result: Result<String, Error>
    ) {
        self.tokenStore = tokenStore
        self.result = result
    }

    var hasStoredCredentialMaterial: Bool {
        let accessToken = tokenStore.getToken(key: .accessToken)
        let refreshToken = tokenStore.getToken(key: .refreshToken)
        return !(accessToken?.isEmpty ?? true) || !(refreshToken?.isEmpty ?? true)
    }

    var refreshCallCount: Int {
        lock.withLock { refreshCalls }
    }

    func accessToken(for authentication: RequestAuthentication) async throws -> String? {
        guard authentication != .none else { return nil }
        if let token = tokenStore.getToken(key: .accessToken) {
            return token
        }
        return try await refreshAfterRejection(rejectedAccessToken: nil)
    }

    func refreshAfterRejection(rejectedAccessToken: String?) async throws -> String {
        _ = rejectedAccessToken
        lock.withLock { refreshCalls += 1 }
        switch result {
        case .success(let token):
            tokenStore.saveToken(token, key: .accessToken)
            return token
        case .failure(let error):
            throw error
        }
    }
}

private final class OpenAIServiceCapturedRequests: @unchecked Sendable {
    private let lock = NSLock()
    private var capturedAuthorizations: [String?] = []
    private var capturedContentTypes: [String?] = []
    private var capturedBodies: [Data?] = []

    var count: Int {
        lock.withLock { capturedAuthorizations.count }
    }

    var authorizations: [String?] {
        lock.withLock { capturedAuthorizations }
    }

    var contentTypes: [String?] {
        lock.withLock { capturedContentTypes }
    }

    var bodies: [Data?] {
        lock.withLock { capturedBodies }
    }

    func append(authorization: String?, contentType: String?, body: Data?) {
        lock.withLock {
            capturedAuthorizations.append(authorization)
            capturedContentTypes.append(contentType)
            capturedBodies.append(body)
        }
    }
}

private final class OpenAIServiceURLProtocol: URLProtocol {
    static var requestHandler: ((URLRequest) throws -> (HTTPURLResponse, Data))?

    private static let lock = NSLock()
    private static var capturedRequestCount = 0

    static var requestCount: Int {
        lock.withLock { capturedRequestCount }
    }

    static func reset() {
        lock.withLock { capturedRequestCount = 0 }
        requestHandler = nil
    }

    override class func canInit(with request: URLRequest) -> Bool {
        true
    }

    override class func canonicalRequest(for request: URLRequest) -> URLRequest {
        request
    }

    override func startLoading() {
        Self.lock.withLock { Self.capturedRequestCount += 1 }
        guard let handler = Self.requestHandler else {
            XCTFail("Missing OpenAIServiceURLProtocol handler")
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
