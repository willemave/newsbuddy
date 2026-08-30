import Foundation
import XCTest
@testable import newsly

final class ClientFailureTests: XCTestCase {
    func testClassifiesNestedRefreshConnectivityFailure() {
        let error = AuthError.networkError(URLError(.networkConnectionLost))

        XCTAssertEqual(
            ClientFailure.classify(error),
            .connectivity(.networkConnectionLost)
        )
    }

    func testClassifiesNestedRefreshCancellation() {
        let error = AuthError.networkError(URLError(.cancelled))

        XCTAssertEqual(ClientFailure.classify(error), .cancelled)
    }

    func testClassifiesNSErrorBackedConnectivityFailure() {
        let error = NSError(
            domain: NSURLErrorDomain,
            code: URLError.notConnectedToInternet.rawValue
        )

        XCTAssertEqual(
            ClientFailure.classify(error),
            .connectivity(.notConnectedToInternet)
        )
    }
}
