import XCTest
@testable import newsly

final class BriefingSourceDeckLookupTests: XCTestCase {
    func testNewsMatchesConvertedContentDeckByURLInsteadOfIntegerID() {
        let unrelated = deck(id: 1, contentId: 7, url: "https://example.com/other")
        let converted = deck(id: 2, contentId: 91, url: "https://example.com/article")
        let found = BriefingSourceDeckLookup.find(
            source: .news(7), sourceURL: "http://EXAMPLE.com/article",
            decks: [unrelated, converted]
        )
        XCTAssertEqual(found?.id, 2)
    }

    func testContentMatchesOnlyItsIDAndDoesNotBorrowSameURLDeck() {
        let sameURL = deck(id: 1, contentId: 91, url: "https://example.com/article")
        let sameID = deck(id: 2, contentId: 7, url: "https://example.com/other")
        XCTAssertNil(BriefingSourceDeckLookup.find(
            source: .content(7), sourceURL: "https://example.com/article", decks: [sameURL]
        ))
        XCTAssertEqual(BriefingSourceDeckLookup.find(
            source: .content(7), sourceURL: "https://example.com/article", decks: [sameURL, sameID]
        )?.id, 2)
    }

    func testNewsDoesNotMatchDifferentQueryOrPath() {
        let candidate = deck(id: 1, contentId: 91, url: "https://example.com/article?edition=1")
        for url in ["https://example.com/article?edition=2", "https://example.com/other?edition=1"] {
            XCTAssertNil(BriefingSourceDeckLookup.find(
                source: .news(7), sourceURL: url, decks: [candidate]
            ), url)
        }
    }

    func testMissingNewsOrDeckURLCannotMatch() {
        let candidate = deck(id: 1, contentId: 7, url: nil)
        XCTAssertNil(BriefingSourceDeckLookup.find(source: .news(7), sourceURL: nil, decks: [candidate]))
        XCTAssertNil(BriefingSourceDeckLookup.find(
            source: .news(7), sourceURL: "https://example.com/article", decks: [candidate]
        ))
    }

    func testNormalizationAcceptsHTTPBareAndProtocolRelativeURLs() {
        for raw in [
            "http://EXAMPLE.com/article", "https://Example.COM/article",
            "example.com/article", "//EXAMPLE.com/article", " \nhttp://example.com/article \n"
        ] {
            XCTAssertEqual(BriefingSourceDeckLookup.normalizedNewsURL(raw), "https://example.com/article", raw)
        }
    }

    func testNormalizationRemovesDefaultPortsAndAddsRootPath() {
        for raw in ["http://EXAMPLE.com:80", "http://example.com:443", "https://example.com:443", "example.com", "//example.com"] {
            XCTAssertEqual(BriefingSourceDeckLookup.normalizedNewsURL(raw), "https://example.com/", raw)
        }
        XCTAssertEqual(
            BriefingSourceDeckLookup.normalizedNewsURL("http://example.com:8080/article"),
            "https://example.com:8080/article"
        )
    }

    func testUnsupportedSchemesAndEmptyURLsAreRejected() {
        for raw in ["ftp://example.com/article", "file:///article", "mailto:reader@example.com", "", " \n", "https://"] {
            XCTAssertNil(BriefingSourceDeckLookup.normalizedNewsURL(raw), raw)
        }
        XCTAssertNil(BriefingSourceDeckLookup.normalizedNewsURL(nil))
    }

    private func deck(id: Int, contentId: Int, url: String?) -> LearningDeck {
        LearningDeck(
            id: id, title: "Deck", sourceKind: .content, sourceURL: url,
            sourceContentId: contentId, sourceTitle: "Source", sourceMetadata: [:],
            status: nil, shareEnabled: false, viewerAvailable: false,
            latestSuccessfulRunId: nil, latestRun: nil, createdAt: Date(), updatedAt: Date()
        )
    }
}
