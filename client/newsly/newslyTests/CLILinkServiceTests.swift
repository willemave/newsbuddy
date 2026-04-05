import XCTest
@testable import newsly

final class CLILinkServiceTests: XCTestCase {
    func testParseScannedCodeExtractsSessionAndApproveToken() throws {
        let payload = try CLILinkScanPayload.parse(
            from: "newsly://cli-link?session_id=session-123&approve_token=approve-456"
        )

        XCTAssertEqual(payload.sessionID, "session-123")
        XCTAssertEqual(payload.approveToken, "approve-456")
    }

    func testParseScannedCodeRejectsUnexpectedScheme() {
        XCTAssertThrowsError(
            try CLILinkScanPayload.parse(
                from: "https://example.com/cli-link?session_id=session-123&approve_token=approve-456"
            )
        )
    }
}

