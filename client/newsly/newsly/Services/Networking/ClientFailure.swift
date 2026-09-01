import Foundation

struct APIErrorMetadata: Equatable {
    let code: String
    let message: String
    let retryable: Bool
    let requestID: String
    let detailsJSON: Data?

    init(response: APIErrorResponse) {
        code = response.code
        message = response.message
        retryable = response.retryable
        requestID = response.requestId
        if let details = response.details {
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.sortedKeys]
            detailsJSON = try? encoder.encode(details)
        } else {
            detailsJSON = nil
        }
    }
}

/// The transport-level failure vocabulary shared by app and extension requests.
enum ClientFailure: Error, Equatable {
    case cancelled
    case connectivity(URLError.Code)
    case authenticationRequired
    case authenticationExpired
    case invalidRequest
    case invalidResponse
    case server(statusCode: Int, error: APIErrorMetadata)
    case http(statusCode: Int, detail: String?)
    case decoding(endpoint: String)
    case unexpected

    /// Recursively flattens system and transitional auth errors so lifecycle
    /// cancellation and wake-time connectivity failures have one meaning.
    static func classify(_ error: Error) -> ClientFailure {
        classify(error, depth: 0)
    }

    private static func classify(_ error: Error, depth: Int) -> ClientFailure {
        guard depth < 12 else { return .unexpected }

        if let failure = error as? ClientFailure {
            return failure
        }
        if let convertible = error as? any ClientFailureConvertible {
            return convertible.asClientFailure()
        }
        if error is CancellationError {
            return .cancelled
        }
        if let urlError = error as? URLError {
            return urlError.code == .cancelled
                ? .cancelled
                : .connectivity(urlError.code)
        }
        let nsError = error as NSError
        if nsError.domain == NSURLErrorDomain {
            let code = URLError.Code(rawValue: nsError.code)
            return code == .cancelled ? .cancelled : .connectivity(code)
        }
        if let underlying = nsError.userInfo[NSUnderlyingErrorKey] as? Error {
            return classify(underlying, depth: depth + 1)
        }
        return .unexpected
    }
}

protocol ClientFailureConvertible: Error {
    func asClientFailure() -> ClientFailure
}

extension ClientFailure: LocalizedError {
    var errorDescription: String? {
        switch self {
        case .cancelled:
            return "The request was cancelled."
        case .connectivity:
            return "Newsbuddy could not reach the network."
        case .authenticationRequired:
            return "Sign in to continue."
        case .authenticationExpired:
            return "Your session expired. Sign in again to continue."
        case .invalidRequest:
            return "The request was invalid."
        case .invalidResponse:
            return "Newsbuddy received an invalid response."
        case .server(_, let error):
            return error.message
        case .http(let statusCode, let detail):
            if let detail {
                let trimmed = detail.trimmingCharacters(in: .whitespacesAndNewlines)
                if !trimmed.isEmpty {
                    return trimmed
                }
            }
            return "HTTP error: \(statusCode)"
        case .decoding:
            return "Newsbuddy could not read the server response."
        case .unexpected:
            return "An unexpected error occurred."
        }
    }
}
