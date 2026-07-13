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
        XCTAssertThrowsError(try decode(APIBriefingTier.self, rawValue: "future_tier"))
        XCTAssertThrowsError(try decode(APIBriefingBlockType.self, rawValue: "future_block"))
        XCTAssertThrowsError(
            try decode(APIBriefingFigurePlacement.self, rawValue: "future_placement")
        )
        XCTAssertThrowsError(try decode(APIBriefingRunKind.self, rawValue: "future_run"))
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
        XCTAssertEqual(appSummary.contentType, .article)
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
        XCTAssertEqual(appFuture.contentType.rawValue, "future_content_type")
        XCTAssertEqual(appFuture.status.rawValue, "future_status")
        XCTAssertFalse(APIContentType.knownCases.contains(appFuture.contentType))
        XCTAssertFalse(APIContentStatus.knownCases.contains(appFuture.status))

        let detail = try decodeFixture(APIContentDetailResponse.self, "content_detail_null_optionals.json")
        XCTAssertNil(detail.summaryKind)
        XCTAssertNil(detail.summaryVersion)
        XCTAssertEqual(detail.detectedFeed?.url, "https://newsletter.example.com/feed")
        XCTAssertEqual(detail.canSubscribe, true)
    }

    func testRequiredDatetimeFieldThrowsOnUnparseableValue() {
        let json = """
        {
            "id": 1,
            "scraper_type": "rss",
            "display_name": "Example Feed",
            "config": {},
            "feed_url": "https://example.com/feed.xml",
            "limit": null,
            "is_active": true,
            "created_at": "not-a-date",
            "stats": null
        }
        """
        let data = Data(json.utf8)

        XCTAssertThrowsError(try JSONDecoder().decode(APIScraperConfigResponse.self, from: data)) { error in
            guard case DecodingError.dataCorrupted = error else {
                return XCTFail("Expected DecodingError.dataCorrupted, got \(error)")
            }
        }
    }

    func testOptionalDatetimeFieldThrowsOnUnparseablePresentValue() {
        let json = """
        {
            "provider": "x",
            "connected": true,
            "is_active": true,
            "provider_user_id": null,
            "provider_username": null,
            "scopes": [],
            "last_synced_at": "not-a-date",
            "last_status": null,
            "last_error": null,
            "twitter_username": null
        }
        """
        let data = Data(json.utf8)

        XCTAssertThrowsError(try JSONDecoder().decode(APIXConnectionResponse.self, from: data)) { error in
            guard case DecodingError.dataCorrupted = error else {
                return XCTFail("Expected DecodingError.dataCorrupted, got \(error)")
            }
        }
    }

    func testOptionalDatetimeFieldDefaultsToNilWhenMissing() throws {
        let json = """
        {
            "provider": "x",
            "connected": false,
            "is_active": false,
            "provider_user_id": null,
            "provider_username": null,
            "scopes": [],
            "last_status": null,
            "last_error": null,
            "twitter_username": null
        }
        """
        let data = Data(json.utf8)

        let decoded = try JSONDecoder().decode(APIXConnectionResponse.self, from: data)
        XCTAssertNil(decoded.lastSyncedAt)
    }

    func testCanonicalDatetimeFixtureDecodesToExpectedDate() throws {
        let json = """
        {
            "id": 1,
            "scraper_type": "rss",
            "display_name": "Example Feed",
            "config": {},
            "feed_url": "https://example.com/feed.xml",
            "limit": null,
            "is_active": true,
            "created_at": "2026-04-27T12:00:00Z",
            "stats": null
        }
        """
        let data = Data(json.utf8)

        let decoded = try JSONDecoder().decode(APIScraperConfigResponse.self, from: data)
        let expected = try XCTUnwrap(ServerDate.parse("2026-04-27T12:00:00Z"))
        XCTAssertEqual(decoded.createdAt, expected)
    }

    func testDatetimeFieldEncodeDecodeRoundTripPreservesValue() throws {
        let original = APIScraperConfigResponse(
            id: 42,
            scraperType: "rss",
            displayName: "Round Trip Feed",
            config: [:],
            feedUrl: "https://example.com/feed.xml",
            limit: nil,
            isActive: true,
            createdAt: try XCTUnwrap(ServerDate.parse("2026-04-27T12:00:00.123456Z")),
            stats: nil
        )

        let data = try JSONEncoder().encode(original)
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertTrue((json["created_at"] as? String)?.hasSuffix("Z") == true)

        let roundTripped = try JSONDecoder().decode(APIScraperConfigResponse.self, from: data)
        XCTAssertEqual(roundTripped.createdAt, original.createdAt)
    }

    // Domain models that decode through generated wire models must also encode their
    // Date fields as canonical server strings; synthesized Encodable would silently
    // emit numeric timestamps instead.
    func testChatMessageEncodesTimestampAsServerDateString() throws {
        let message = ChatMessage(
            id: 7,
            role: .user,
            timestamp: try XCTUnwrap(ServerDate.parse("2026-07-02T08:30:00.250Z")),
            content: "hello"
        )

        let data = try JSONEncoder().encode(message)
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        let timestamp = try XCTUnwrap(json["timestamp"] as? String)
        XCTAssertEqual(ServerDate.parse(timestamp), message.timestamp)
    }

    func testChatSessionSummaryEncodesDatesAsServerDateStrings() throws {
        let summary = ChatSessionSummary(
            id: 3,
            contentId: nil,
            title: "Session",
            sessionType: nil,
            topic: nil,
            llmProvider: "anthropic",
            llmModel: "claude",
            createdAt: try XCTUnwrap(ServerDate.parse("2026-07-01T10:00:00.000Z")),
            updatedAt: nil,
            lastMessageAt: try XCTUnwrap(ServerDate.parse("2026-07-02T11:15:30.500Z")),
            articleTitle: nil,
            articleUrl: nil,
            articleSummary: nil,
            articleSource: nil,
            hasPendingMessage: nil,
            isSavedToKnowledge: nil,
            hasMessages: nil,
            lastMessagePreview: nil,
            lastMessageRole: nil
        )

        let data = try JSONEncoder().encode(summary)
        let json = try XCTAssertUnwrapJSONObject(data)
        let createdAt = try XCTUnwrap(json["created_at"] as? String)
        let lastMessageAt = try XCTUnwrap(json["last_message_at"] as? String)
        XCTAssertEqual(ServerDate.parse(createdAt), summary.createdAt)
        XCTAssertEqual(ServerDate.parse(lastMessageAt), summary.lastMessageAt)
        XCTAssertNil(json["updated_at"], "nil optional dates must stay omitted")
    }

    private func XCTAssertUnwrapJSONObject(_ data: Data) throws -> [String: Any] {
        try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
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
