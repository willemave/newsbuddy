import Foundation

extension AuthError: ClientFailureConvertible {
    func asClientFailure() -> ClientFailure {
        switch self {
        case .networkError(let underlying):
            return ClientFailure.classify(underlying)
        case .notAuthenticated:
            return .authenticationRequired
        case .serverError(let statusCode, let message):
            return .http(statusCode: statusCode, detail: message)
        case .refreshFailed, .appleSignInFailed:
            return .unexpected
        }
    }
}
