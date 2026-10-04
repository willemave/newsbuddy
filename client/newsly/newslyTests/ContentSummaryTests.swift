import XCTest
@testable import newsly

final class ContentSummaryTests: XCTestCase {
    func testGeneratedMappingPreservesSavedSourceForBookmarkFilters() throws {
        let summary = try decodeSummary(overrides: [
            "content_type": "article",
            "is_saved_to_knowledge": true,
            "saved_source": "x_bookmark",
        ])

        XCTAssertEqual(summary.savedSource, "x_bookmark")
        XCTAssertTrue(summary.isXBookmark)
        XCTAssertEqual(summary.updating(isRead: true).savedSource, "x_bookmark")
    }

    func testKnowledgeSourceLabelsUseSpecificSource() {
        XCTAssertEqual(makeSummary(source: "arxiv.org").knowledgeSourceLabels, ["arxiv.org"])
    }

    func testKnowledgeSourceLabelsReplaceSelfSubmissionWithURLHost() {
        let summary = makeSummary(
            url: "https://www.example.com/research/paper",
            source: "self submission"
        )

        XCTAssertEqual(summary.knowledgeSourceLabels, ["example.com"])
    }

    func testKnowledgeSourceLabelsKeepXOriginAndSpecificPublisher() {
        let summary = makeSummary(source: "Example Publisher", savedSource: "x_bookmark")

        XCTAssertEqual(summary.knowledgeSourceLabels, ["X Bookmark", "Example Publisher"])
    }

    func testNewsListSummaryRemainsHidden() throws {
        let summary = try decodeSummary(overrides: ["short_summary": "Hidden list summary"])

        XCTAssertEqual(summary.shortSummary, "Hidden list summary")
        XCTAssertNil(summary.summaryDisplayText)
    }

    func testArticleSummaryDisplays() {
        XCTAssertEqual(makeSummary(contentType: .article).summaryDisplayText, "Summary")
    }

    func testProcessedDateDisplayUsesProcessedTimestamp() {
        let summary = makeSummary(processedAt: "2026-03-18T06:00:00Z")

        XCTAssertNotNil(summary.processedDateDisplay)
    }

    func testSavedLibraryStateTracksTerminalStatus() {
        for status: APIContentStatus in [.new, .pending, .processing, .awaiting_image] {
            XCTAssertEqual(makeSummary(status: status).savedLibraryItemState, .processing)
        }
        XCTAssertEqual(makeSummary(status: .completed).savedLibraryItemState, .ready)
        XCTAssertEqual(makeSummary(status: .failed).savedLibraryItemState, .unavailable)
        XCTAssertEqual(makeSummary(status: .skipped).savedLibraryItemState, .unavailable)
        XCTAssertEqual(
            makeSummary(status: .unknown("future_status")).savedLibraryItemState,
            .unavailable
        )
    }

    private func makeSummary(
        contentType: APIContentType = .news,
        status: APIContentStatus = .completed,
        url: String = "https://example.com/story",
        source: String? = "Example",
        savedSource: String? = nil,
        processedAt: String? = nil
    ) -> ContentSummary {
        ContentSummary(
            id: 7,
            contentType: contentType,
            url: url,
            title: "Example story",
            source: source,
            platform: "web",
            status: status,
            shortSummary: "Summary",
            createdAt: "2026-03-18T05:00:00Z",
            processedAt: processedAt,
            isRead: false,
            isSavedToKnowledge: false,
            savedSource: savedSource
        )
    }

    private func decodeSummary(overrides: [String: Any]) throws -> ContentSummary {
        var payload: [String: Any] = [
            "id": 7,
            "content_type": "news",
            "url": "https://example.com/story",
            "source_url": NSNull(),
            "discussion_url": NSNull(),
            "title": "Example story",
            "source": "Example",
            "platform": "web",
            "status": "completed",
            "short_summary": "Summary",
            "created_at": "2026-03-18T05:00:00Z",
            "processed_at": NSNull(),
            "classification": NSNull(),
            "publication_date": NSNull(),
            "is_read": false,
            "is_saved_to_knowledge": false,
            "knowledge_saved_at": NSNull(),
            "news_article_url": NSNull(),
            "news_discussion_url": NSNull(),
            "news_key_points": NSNull(),
            "news_summary": NSNull(),
            "user_status": NSNull(),
            "image_url": NSNull(),
            "thumbnail_url": NSNull(),
            "primary_topic": NSNull(),
            "top_comment": NSNull(),
            "comment_count": NSNull(),
            "feed_preview": NSNull(),
            "artifact_type": NSNull(),
            "preview_bullets": NSNull(),
            "reason_to_read": NSNull(),
            "key_takeaway": NSNull(),
            "saved_source": NSNull(),
        ]
        payload.merge(overrides) { _, replacement in replacement }
        let data = try JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys])
        return ContentSummary(api: try JSONDecoder().decode(APIContentSummaryResponse.self, from: data))
    }
}
