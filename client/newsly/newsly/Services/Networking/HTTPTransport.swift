import Foundation

/// Executes already-prepared HTTP requests without applying application policy.
struct HTTPTransport {
    private let session: URLSession

    init(session: URLSession) {
        self.session = session
    }

    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let (data, response) = try await session.data(for: request)
        guard let response = response as? HTTPURLResponse else {
            throw HTTPTransportError.invalidResponse
        }
        return (data, response)
    }
}

enum HTTPTransportError: Error {
    case invalidResponse
}
