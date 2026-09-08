import XCTest
@testable import newsly

@MainActor
final class ArtifactResponseFixtureTests: XCTestCase {
    func testSharedArtifactResponsesDecodeAndPreserveSharing() throws {
        let repoRoot = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent()
        let data = try Data(contentsOf: repoRoot.appendingPathComponent("contracts/testing/content/artifact_responses.json"))
        let fixtures = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [[String: Any]])
        XCTAssertEqual(fixtures.count, 4)
        for fixture in fixtures {
            let currentData = try JSONSerialization.data(withJSONObject: XCTUnwrap(fixture["detail"]))
            let previousData = try JSONSerialization.data(withJSONObject: XCTUnwrap(fixture["previous_detail"]))
            let generated = try JSONDecoder().decode(APIContentDetailResponse.self, from: currentData)
            XCTAssertNotNil(generated.longformArtifact)
            XCTAssertNil(generated.structuredSummary)
            let current = try JSONDecoder().decode(ContentDetail.self, from: currentData)
            let previous = try JSONDecoder().decode(ContentDetail.self, from: previousData)
            let artifact = try XCTUnwrap(current.longformArtifact)
            let previousArtifact = try XCTUnwrap(previous.longformArtifact)
            XCTAssertEqual(artifact.artifact.type, fixture["name"] as? String)
            XCTAssertNotNil(artifact.feedPreview)
            XCTAssertNotNil(artifact.selectionTrace)
            XCTAssertEqual(artifact.detailSections.map(\.id), previousArtifact.detailSections.map(\.id))
            for option: ShareContentOption in [.medium, .full] {
                let rendered = try XCTUnwrap(ShareMarkdownBuilder(content: current, contentBody: nil).markdown(for: option))
                XCTAssertFalse(rendered.isEmpty)
                XCTAssertEqual(rendered, ShareMarkdownBuilder(content: previous, contentBody: nil).markdown(for: option))
            }
        }
    }
}
