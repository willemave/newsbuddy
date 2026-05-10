//
//  ContentDiscussionTests.swift
//  newslyTests
//

import XCTest
@testable import newsly

final class ContentDiscussionTests: XCTestCase {
    func testDiscussionListIsRenderableWhenLinksExistWithoutGroups() {
        let discussion = ContentDiscussion(
            contentId: 1,
            status: "completed",
            mode: "discussion_list",
            platform: "techmeme",
            sourceURL: "https://www.techmeme.com",
            discussionURL: "https://www.techmeme.com/story",
            fetchedAt: nil,
            errorMessage: nil,
            comments: [],
            discussionGroups: [],
            links: [
                DiscussionLink(
                    url: "https://news.ycombinator.com/item?id=1",
                    source: "discussion_group",
                    commentID: nil,
                    groupLabel: "Forums",
                    title: "Hacker News"
                )
            ],
            stats: [:]
        )

        XCTAssertTrue(discussion.hasRenderableContent)
    }

    func testUnavailableMessageUsesServerErrorWhenPresent() {
        let discussion = ContentDiscussion(
            contentId: 1,
            status: "failed",
            mode: "none",
            platform: "hackernews",
            sourceURL: nil,
            discussionURL: "https://news.ycombinator.com/item?id=1",
            fetchedAt: nil,
            errorMessage: "  Timed out while fetching comments.  ",
            comments: [],
            discussionGroups: [],
            links: [],
            stats: [:]
        )

        XCTAssertEqual(discussion.unavailableMessage, "Timed out while fetching comments.")
    }

    func testUnavailableMessageExplainsNotReadyState() {
        let discussion = ContentDiscussion(
            contentId: 1,
            status: "not_ready",
            mode: "none",
            platform: "hackernews",
            sourceURL: nil,
            discussionURL: "https://news.ycombinator.com/item?id=1",
            fetchedAt: nil,
            errorMessage: nil,
            comments: [],
            discussionGroups: [],
            links: [],
            stats: [:]
        )

        XCTAssertEqual(discussion.unavailableMessage, "Comments are still being prepared for this story.")
    }

    func testNotReadyDiscussionShouldAutoRefresh() {
        let discussion = ContentDiscussion(
            contentId: 1,
            status: "not_ready",
            mode: "comments",
            platform: "hackernews",
            sourceURL: nil,
            discussionURL: "https://news.ycombinator.com/item?id=1",
            fetchedAt: nil,
            errorMessage: nil,
            comments: [],
            discussionGroups: [],
            links: [],
            stats: [:]
        )

        XCTAssertTrue(discussion.shouldAutoRefresh)
    }

    func testRenderableDiscussionDoesNotAutoRefresh() {
        let discussion = ContentDiscussion(
            contentId: 1,
            status: "completed",
            mode: "comments",
            platform: "hackernews",
            sourceURL: nil,
            discussionURL: "https://news.ycombinator.com/item?id=1",
            fetchedAt: nil,
            errorMessage: nil,
            comments: [
                DiscussionComment(
                    commentID: "1",
                    parentID: nil,
                    author: "alice",
                    text: "Useful comment.",
                    compactText: "Useful comment.",
                    depth: 0,
                    createdAt: nil,
                    sourceURL: nil
                )
            ],
            discussionGroups: [],
            links: [],
            stats: [:]
        )

        XCTAssertFalse(discussion.shouldAutoRefresh)
    }
}
