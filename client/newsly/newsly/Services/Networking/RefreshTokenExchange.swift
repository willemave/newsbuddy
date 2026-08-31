import Foundation
import os.log

protocol RefreshTokenExchanging: AnyObject {
    func exchange(refreshToken: String, attemptID: String) async throws -> CredentialTokens
}

/// The one unauthenticated refresh operation. It depends only on the mechanical
/// transport seam, so CredentialSession never calls back into APIClient.
final class RefreshTokenExchange: RefreshTokenExchanging {
    private let transport: HTTPTransport
    private let baseURLProvider: () -> URL?
    private let retryDelaysNanoseconds: [UInt64]
    private let logger = Logger(subsystem: "com.newsly", category: "RefreshTokenExchange")

    init(
        transport: HTTPTransport,
        baseURLProvider: @escaping () -> URL?,
        retryDelaysNanoseconds: [UInt64] = [250_000_000, 750_000_000]
    ) {
        self.transport = transport
        self.baseURLProvider = baseURLProvider
        self.retryDelaysNanoseconds = retryDelaysNanoseconds
    }

    func exchange(refreshToken: String, attemptID: String) async throws -> CredentialTokens {
        guard let baseURL = baseURLProvider(),
              let url = URL(string: "/auth/refresh", relativeTo: baseURL)?.absoluteURL else {
            throw ClientFailure.invalidRequest
        }

        var request = URLRequest(url: url)
        request.httpMethod = HTTPMethod.post.rawValue
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(
            APIRefreshTokenRequest(refreshToken: refreshToken, attemptId: attemptID)
        )

        let (data, response) = try await sendReplaySafe(request)
        switch response.statusCode {
        case 200:
            do {
                let response = try JSONDecoder().decode(APIAccessTokenResponse.self, from: data)
                return CredentialTokens(
                    accessToken: response.accessToken,
                    refreshToken: response.refreshToken
                )
            } catch {
                throw ClientFailure.decoding(endpoint: "/auth/refresh")
            }
        case 401, 403:
            throw ClientFailure.authenticationExpired
        default:
            throw HTTPResponseDetail.failure(statusCode: response.statusCode, data: data)
        }
    }

    private func sendReplaySafe(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        var retryIndex = 0
        while true {
            do {
                return try await transport.send(request)
            } catch {
                let failure = ClientFailure.classify(error)
                if failure == .cancelled {
                    throw ClientFailure.cancelled
                }
                guard case .connectivity(let code) = failure,
                      retryableConnectivityCodes.contains(code),
                      retryIndex < retryDelaysNanoseconds.count else {
                    throw failure
                }
                let delay = retryDelaysNanoseconds[retryIndex]
                retryIndex += 1
                logger.info(
                    "Replay-safe refresh retry scheduled | attempt=\(retryIndex, privacy: .public) code=\(code.rawValue, privacy: .public) delay_ms=\(delay / 1_000_000, privacy: .public)"
                )
                try await Task.sleep(nanoseconds: delay)
            }
        }
    }
}

enum HTTPResponseDetail {
    static func errorResponse(from data: Data) -> APIErrorResponse? {
        guard !data.isEmpty else { return nil }
        return try? JSONDecoder().decode(APIErrorResponse.self, from: data)
    }

    static func extract(from data: Data) -> String? {
        guard !data.isEmpty else { return nil }
        if let errorResponse = errorResponse(from: data) {
            return errorResponse.message
        }
        if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            for key in ["detail", "message", "error", "error_message"] {
                if let value = json[key] {
                    return String(describing: value).prefix(240).description
                }
            }
        }
        guard let raw = String(data: data, encoding: .utf8)?
            .trimmingCharacters(in: .whitespacesAndNewlines),
              !raw.isEmpty else {
            return nil
        }
        return raw.prefix(240).description
    }

    static func failure(statusCode: Int, data: Data) -> ClientFailure {
        if let response = errorResponse(from: data) {
            return .server(
                statusCode: statusCode,
                error: APIErrorMetadata(response: response)
            )
        }
        return .http(statusCode: statusCode, detail: extract(from: data))
    }
}
