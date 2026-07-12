import XCTest
@testable import newsly

final class DebugLoginLinkTests: XCTestCase {
    func testParsesDebugLoginURL() throws {
        let url = try XCTUnwrap(
            URL(string: "newsly://debug-login?user_id=42&host=127.0.0.1&port=8000&https=false")
        )

        let link = try XCTUnwrap(DebugLoginLink(url: url))

        XCTAssertEqual(link.userID, 42)
        XCTAssertEqual(link.serverHost, "127.0.0.1")
        XCTAssertEqual(link.serverPort, "8000")
        XCTAssertFalse(link.useHTTPS)
    }

    func testRejectsIncompleteOrInvalidDebugLoginURL() throws {
        XCTAssertNil(DebugLoginLink(url: try XCTUnwrap(URL(string: "newsly://debug-login?user_id=42"))))
        XCTAssertNil(
            DebugLoginLink(
                url: try XCTUnwrap(
                    URL(string: "newsly://debug-login?user_id=0&host=localhost&port=8000&https=false")
                )
            )
        )
        XCTAssertNil(
            DebugLoginLink(
                url: try XCTUnwrap(
                    URL(string: "newsly://cli-link?user_id=42&host=localhost&port=8000&https=false")
                )
            )
        )
    }
}
