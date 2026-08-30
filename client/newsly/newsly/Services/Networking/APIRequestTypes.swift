import Foundation

enum ResponseDecoding: Sendable {
    case standard
    case iso8601
}

struct AuthorizedMediaResource {
    let url: URL
    let headers: [String: String]
}
