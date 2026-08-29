import XCTest
@testable import newsly

final class LearningDeckAPIMappingTests: XCTestCase {
    func testResponseMapsGeneratedWireContractIntoDomainModel() {
        let createdAt = Date(timeIntervalSince1970: 10)
        let updatedAt = Date(timeIntervalSince1970: 20)
        let response = APILearningDeckResponse(
            id: 41,
            title: "Generated deck",
            sourceKind: .github_repo,
            sourceUrl: "https://github.com/example/repository",
            sourceContentId: 17,
            sourceTitle: "Repository",
            sourceMetadata: ["language": AnyCodable("Swift")],
            status: .generating,
            shareEnabled: true,
            viewerAvailable: false,
            sourceNotesAvailable: true,
            thumbnailUrl: "/learning/signed/token/assets/thumbnail.png",
            latestSuccessfulRunId: 6,
            latestRun: APILearningDeckRunResponse(
                id: 7,
                status: .validating,
                interestsPrompt: "Focus on architecture",
                timeline: [
                    APILearningDeckTimelineEntry(
                        status: .preparing,
                        note: "Collecting files",
                        createdAt: createdAt
                    )
                ],
                errorMessage: nil,
                startedAt: createdAt,
                completedAt: nil,
                createdAt: createdAt,
                updatedAt: updatedAt
            ),
            createdAt: createdAt,
            updatedAt: updatedAt
        )

        let deck = LearningDeck(apiResponse: response)

        XCTAssertEqual(deck.id, 41)
        XCTAssertEqual(deck.sourceKind, .githubRepo)
        XCTAssertEqual(deck.status, .generating)
        XCTAssertEqual(deck.latestRun?.status, .validating)
        XCTAssertEqual(deck.latestRun?.timeline.first?.status, .preparing)
        XCTAssertEqual(deck.latestRun?.timeline.first?.note, "Collecting files")
        XCTAssertEqual(deck.createdAt, createdAt)
        XCTAssertEqual(deck.updatedAt, updatedAt)
        XCTAssertTrue(deck.shareEnabled)
        XCTAssertTrue(deck.sourceNotesAvailable)
        XCTAssertEqual(deck.thumbnailURL, "/learning/signed/token/assets/thumbnail.png")
        XCTAssertEqual(deck.thumbnailCacheIdentifier, "learning-deck:41:attempt:6")
    }

    func testMappingPreservesUnknownContractValues() {
        let createdAt = Date(timeIntervalSince1970: 10)
        let response = APILearningDeckResponse(
            id: 41,
            title: "Future deck",
            sourceKind: .unknown("future_source"),
            status: .unknown("future_status"),
            latestRun: APILearningDeckRunResponse(
                id: 7,
                status: .unknown("future_run_status"),
                createdAt: createdAt
            ),
            createdAt: createdAt
        )

        let deck = LearningDeck(apiResponse: response)

        XCTAssertEqual(deck.sourceKind, .unknown("future_source"))
        XCTAssertEqual(deck.status, .unknown("future_status"))
        XCTAssertEqual(deck.latestRun?.status, .unknown("future_run_status"))
    }

    func testShareResponseMapsGeneratedWireNamingOnce() {
        let response = APILearningDeckShareResponse(
            shareEnabled: true,
            shareUrl: "https://example.com/learning/shared"
        )

        let mapped = LearningDeckShareResponse(apiResponse: response)

        XCTAssertTrue(mapped.shareEnabled)
        XCTAssertEqual(mapped.shareURL, "https://example.com/learning/shared")
    }
}
