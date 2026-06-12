import XCTest
@testable import newsly

private protocol OpenContractEnum: Decodable, Equatable {
    static var knownCases: [Self] { get }
    var rawValue: String { get }
}

extension APIContentType: OpenContractEnum {}
extension APIContentStatus: OpenContractEnum {}
extension APITaskType: OpenContractEnum {}
extension APITaskStatus: OpenContractEnum {}
extension APISummaryKind: OpenContractEnum {}
extension APISubmissionOutcome: OpenContractEnum {}
extension APIFeedType: OpenContractEnum {}
extension APIFeedFormat: OpenContractEnum {}
extension APIAudioEpisodeKind: OpenContractEnum {}
extension APIAudioEpisodeStatus: OpenContractEnum {}
extension APICliLinkStatus: OpenContractEnum {}
extension APIOnboardingSuggestionType: OpenContractEnum {}
extension APIOnboardingSelectedSourceType: OpenContractEnum {}
extension APINewsItemVisibilityScope: OpenContractEnum {}
extension APINewsItemStatus: OpenContractEnum {}
extension APILearningDeckSourceKind: OpenContractEnum {}
extension APILearningDeckRunStatus: OpenContractEnum {}
extension APILearningDeckStatus: OpenContractEnum {}
extension APIMessageProcessingStatus: OpenContractEnum {}
extension APIChatMessageDisplayType: OpenContractEnum {}

final class APIContractsGeneratedTests: XCTestCase {
    func testOpenEnumsDecodeUnknownRawValues() throws {
        try assertUnknown(APIContentType.self, rawValue: "future_content_type")
        try assertUnknown(APIContentStatus.self, rawValue: "future_content_status")
        try assertUnknown(APITaskType.self, rawValue: "future_task_type")
        try assertUnknown(APITaskStatus.self, rawValue: "future_task_status")
        try assertUnknown(APISummaryKind.self, rawValue: "future_summary_kind")
        try assertUnknown(APISubmissionOutcome.self, rawValue: "future_submission_outcome")
        try assertUnknown(APIFeedType.self, rawValue: "future_feed_type")
        try assertUnknown(APIFeedFormat.self, rawValue: "future_feed_format")
        try assertUnknown(APIAudioEpisodeKind.self, rawValue: "future_audio_kind")
        try assertUnknown(APIAudioEpisodeStatus.self, rawValue: "future_audio_status")
        try assertUnknown(APICliLinkStatus.self, rawValue: "future_cli_link_status")
        try assertUnknown(APIOnboardingSuggestionType.self, rawValue: "future_onboarding_suggestion")
        try assertUnknown(
            APIOnboardingSelectedSourceType.self,
            rawValue: "future_onboarding_source"
        )
        try assertUnknown(APINewsItemVisibilityScope.self, rawValue: "future_visibility")
        try assertUnknown(APINewsItemStatus.self, rawValue: "future_news_item_status")
        try assertUnknown(APILearningDeckSourceKind.self, rawValue: "future_deck_source")
        try assertUnknown(APILearningDeckRunStatus.self, rawValue: "future_deck_run_status")
        try assertUnknown(APILearningDeckStatus.self, rawValue: "future_deck_status")
        try assertUnknown(APIMessageProcessingStatus.self, rawValue: "future_message_status")
        try assertUnknown(APIChatMessageDisplayType.self, rawValue: "future_display_type")
    }

    func testClosedEnumsRejectUnknownRawValues() {
        XCTAssertThrowsError(try decode(APIContentClassification.self, rawValue: "future_class"))
        XCTAssertThrowsError(try decode(APIChatMessageRole.self, rawValue: "future_role"))
        XCTAssertThrowsError(try decode(APILLMProvider.self, rawValue: "future_provider"))
    }

    func testSubmitContentRequestEncodesContractKeys() throws {
        let request = APISubmitContentRequest(
            url: "https://open.spotify.com/episode/abc123",
            contentType: .podcast,
            title: "Great interview about AI",
            platform: "spotify",
            instruction: "Add all links mentioned in the episode page",
            crawlLinks: true,
            subscribeToFeed: false,
            shareAndChat: true,
            chatInitialMessage: "What should I notice here?",
            saveToKnowledgeAndMarkRead: true
        )

        let data = try JSONEncoder().encode(request)
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])

        XCTAssertEqual(json["url"] as? String, "https://open.spotify.com/episode/abc123")
        XCTAssertEqual(json["content_type"] as? String, "podcast")
        XCTAssertEqual(json["title"] as? String, "Great interview about AI")
        XCTAssertEqual(json["platform"] as? String, "spotify")
        XCTAssertEqual(json["instruction"] as? String, "Add all links mentioned in the episode page")
        XCTAssertEqual(json["crawl_links"] as? Bool, true)
        XCTAssertEqual(json["subscribe_to_feed"] as? Bool, false)
        XCTAssertEqual(json["share_and_chat"] as? Bool, true)
        XCTAssertEqual(json["chat_initial_message"] as? String, "What should I notice here?")
        XCTAssertEqual(json["save_to_knowledge_and_mark_read"] as? Bool, true)
        XCTAssertNil(json["contentType"])
        XCTAssertNil(json["crawlLinks"])
    }

    func testContentFixturesDecodeThroughGeneratedModels() throws {
        let summary = try decodeFixture(APIContentSummaryResponse.self, "content_summary_article.json")
        XCTAssertEqual(summary.id, 101)
        XCTAssertEqual(summary.contentType, .article)
        XCTAssertEqual(summary.status, .completed)
        XCTAssertEqual(summary.savedSource, .knowledge)

        let appSummary = try decodeFixture(ContentSummary.self, "content_summary_article.json")
        XCTAssertEqual(appSummary.contentType, "article")
        XCTAssertEqual(appSummary.isSavedToKnowledge, true)

        let detail = try decodeFixture(ContentDetail.self, "content_detail_long_read.json")
        XCTAssertEqual(detail.id, 401)
        XCTAssertEqual(detail.summaryKind, "long_interleaved")
        XCTAssertEqual(detail.bulletPoints.first?.text, "First point")
    }

    func testAdversarialFixturesDecodeUnknownEnumsAndNullOptionals() throws {
        let future = try decodeFixture(APIContentSummaryResponse.self, "content_summary_unknown_enum.json")
        XCTAssertEqual(future.contentType.rawValue, "future_content_type")
        XCTAssertEqual(future.status.rawValue, "future_status")
        XCTAssertFalse(APIContentType.knownCases.contains(future.contentType))
        XCTAssertFalse(APIContentStatus.knownCases.contains(future.status))

        let appFuture = try decodeFixture(ContentSummary.self, "content_summary_unknown_enum.json")
        XCTAssertEqual(appFuture.contentType, "future_content_type")
        XCTAssertEqual(appFuture.status, "future_status")

        let detail = try decodeFixture(APIContentDetailResponse.self, "content_detail_null_optionals.json")
        XCTAssertNil(detail.summaryKind)
        XCTAssertNil(detail.summaryVersion)
        XCTAssertEqual(detail.detectedFeed?.url, "https://newsletter.example.com/feed")
        XCTAssertEqual(detail.canSubscribe, true)
    }

    private func assertUnknown<T: OpenContractEnum>(
        _ type: T.Type,
        rawValue: String,
        file: StaticString = #filePath,
        line: UInt = #line
    ) throws {
        let decoded = try decode(type, rawValue: rawValue)

        XCTAssertEqual(decoded.rawValue, rawValue, file: file, line: line)
        XCTAssertFalse(T.knownCases.contains(decoded), file: file, line: line)
    }

    private func decode<T: Decodable>(_ type: T.Type, rawValue: String) throws -> T {
        let data = Data("\"\(rawValue)\"".utf8)
        return try JSONDecoder().decode(type, from: data)
    }

    private func decodeFixture<T: Decodable>(_ type: T.Type, _ name: String) throws -> T {
        try JSONDecoder().decode(type, from: fixtureData(name))
    }

    private func fixtureData(_ name: String) throws -> Data {
        try Data(contentsOf: fixtureURL(name))
    }

    private func fixtureURL(_ name: String) -> URL {
        let testFile = URL(fileURLWithPath: #filePath)
        let repoRoot = testFile
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        return repoRoot
            .appendingPathComponent("tests/fixtures/contracts")
            .appendingPathComponent(name)
    }
}
