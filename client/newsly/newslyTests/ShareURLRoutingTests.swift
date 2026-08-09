//
//  ShareURLRoutingTests.swift
//  newslyTests
//
//  Created by Assistant on 2/19/26.
//

import XCTest
@testable import newsly

final class ShareURLRoutingTests: XCTestCase {
    func testApplePodcastDetectedAsApplePodcastShare() {
        let url = URL(string: "https://podcasts.apple.com/us/podcast/example/id12345?i=67890")!

        let match = ShareURLRouting.handler(for: url)

        XCTAssertEqual(match.kind, .applePodcastShare)
        XCTAssertEqual(match.platform, "apple_podcasts")
    }

    func testSpotifyEpisodeDetectedAsPodcastPlatformShare() {
        let url = URL(string: "https://open.spotify.com/episode/abc123?si=xyz")!

        let match = ShareURLRouting.handler(for: url)

        XCTAssertEqual(match.kind, .podcastPlatformShare)
        XCTAssertEqual(match.platform, "spotify")
    }

    func testYouTubeSingleVideoDetectedAsSingleVideoHandler() {
        let url = URL(string: "https://www.youtube.com/watch?v=abc123")!

        let match = ShareURLRouting.handler(for: url)

        XCTAssertEqual(match.kind, .youtubeSingleVideo)
        XCTAssertEqual(match.platform, "youtube")
    }

    func testYoutuBeShortLinkDetectedAsSingleVideoHandler() {
        let url = URL(string: "https://youtu.be/abc123?t=42")!

        let match = ShareURLRouting.handler(for: url)

        XCTAssertEqual(match.kind, .youtubeSingleVideo)
        XCTAssertEqual(match.platform, "youtube")
    }

    func testYouTubeShortsDetectedAsSingleVideoHandler() {
        let url = URL(string: "https://www.youtube.com/shorts/abc123")!

        let match = ShareURLRouting.handler(for: url)

        XCTAssertEqual(match.kind, .youtubeSingleVideo)
        XCTAssertEqual(match.platform, "youtube")
    }

    func testYouTubeLiveDetectedAsSingleVideoHandler() {
        let url = URL(string: "https://www.youtube.com/live/abc123")!

        let match = ShareURLRouting.handler(for: url)

        XCTAssertEqual(match.kind, .youtubeSingleVideo)
        XCTAssertEqual(match.platform, "youtube")
    }

    func testYouTubeEmbedDetectedAsSingleVideoHandler() {
        let url = URL(string: "https://www.youtube.com/embed/abc123")!

        let match = ShareURLRouting.handler(for: url)

        XCTAssertEqual(match.kind, .youtubeSingleVideo)
        XCTAssertEqual(match.platform, "youtube")
    }

    func testYouTubeLegacyVDetectedAsSingleVideoHandler() {
        let url = URL(string: "https://www.youtube.com/v/abc123")!

        let match = ShareURLRouting.handler(for: url)

        XCTAssertEqual(match.kind, .youtubeSingleVideo)
        XCTAssertEqual(match.platform, "youtube")
    }

    func testYouTubeChannelDetectedAsGenericYouTubeShare() {
        let url = URL(string: "https://www.youtube.com/@openai")!

        let match = ShareURLRouting.handler(for: url)

        XCTAssertEqual(match.kind, .youtubeShare)
        XCTAssertEqual(match.platform, "youtube")
    }

    func testPreferredURLPrioritizesSingleVideoOverChannelURL() {
        let channel = URL(string: "https://www.youtube.com/@openai")!
        let video = URL(string: "https://www.youtube.com/watch?v=abc123")!

        let preferred = ShareURLRouting.preferredURL(current: channel, candidate: video)

        XCTAssertEqual(preferred, video)
    }

    func testExtractURLsDedupesAndKeepsOrder() {
        let text = """
        Watch this https://www.youtube.com/watch?v=abc123 and this profile
        https://www.youtube.com/@openai and again https://www.youtube.com/watch?v=abc123
        """

        let urls = ShareURLRouting.extractURLs(from: text)

        XCTAssertEqual(
            urls,
            [
                URL(string: "https://www.youtube.com/watch?v=abc123")!,
                URL(string: "https://www.youtube.com/@openai")!
            ]
        )
    }

    func testOnlyHTTPAndHTTPSURLsCanBecomeShareTargets() {
        XCTAssertTrue(ShareURLRouting.isWebURL(URL(string: "https://example.com/story")!))
        XCTAssertTrue(ShareURLRouting.isWebURL(URL(string: "http://example.com/story")!))
        XCTAssertFalse(ShareURLRouting.isWebURL(URL(fileURLWithPath: "/tmp/private.pdf")))
        XCTAssertFalse(ShareURLRouting.isWebURL(URL(string: "mailto:reader@example.com")!))
        XCTAssertFalse(ShareURLRouting.isWebURL(URL(string: "newsly://content/42")!))
        XCTAssertFalse(ShareURLRouting.isWebURL(URL(string: "https:///missing-host")!))
        XCTAssertFalse(ShareURLRouting.isWebURL(URL(string: "https://:443/path")!))
        XCTAssertFalse(ShareURLRouting.isWebURL(URL(string: "https://%20/path")!))
    }
}
