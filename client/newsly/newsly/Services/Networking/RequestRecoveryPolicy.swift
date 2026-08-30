import Foundation

/// Typed methods at the request boundary. Only GET and HEAD are eligible for
/// automatic connectivity replay.
enum HTTPMethod: String, Hashable, Sendable {
    case get = "GET"
    case head = "HEAD"
    case post = "POST"
    case put = "PUT"
    case patch = "PATCH"
    case delete = "DELETE"

    var canRetryConnectivityFailure: Bool {
        self == .get || self == .head
    }
}

/// Recovery is opt-in until each feature has parity coverage. The delays are
/// injectable so retry behavior is deterministic in tests.
struct RequestRecoveryPolicy: Equatable, Sendable {
    let connectivityRetryDelaysNanoseconds: [UInt64]

    static let none = RequestRecoveryPolicy(connectivityRetryDelaysNanoseconds: [])
    static let safeRead = RequestRecoveryPolicy(
        connectivityRetryDelaysNanoseconds: [250_000_000, 750_000_000]
    )
}

let retryableConnectivityCodes: Set<URLError.Code> = [
    .timedOut,
    .cannotFindHost,
    .cannotConnectToHost,
    .networkConnectionLost,
    .dnsLookupFailed,
    .notConnectedToInternet,
    .resourceUnavailable,
]

/// One budget is shared by the original request and its one auth replay. Token
/// acquisition and the refresh exchange themselves never consume or restart it.
final class RequestRecoveryBudget: @unchecked Sendable {
    private let lock = NSLock()
    private let delays: [UInt64]
    private var nextDelayIndex = 0

    init(policy: RequestRecoveryPolicy) {
        delays = policy.connectivityRetryDelaysNanoseconds
    }

    func takeDelay(method: HTTPMethod, connectivityCode: URLError.Code) -> UInt64? {
        guard method.canRetryConnectivityFailure,
              retryableConnectivityCodes.contains(connectivityCode) else {
            return nil
        }
        return lock.withLock {
            guard nextDelayIndex < delays.count else { return nil }
            defer { nextDelayIndex += 1 }
            return delays[nextDelayIndex]
        }
    }
}
