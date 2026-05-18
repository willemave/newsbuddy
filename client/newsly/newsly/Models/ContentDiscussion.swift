//
//  ContentDiscussion.swift
//  newsly
//
//  Created by Assistant on 2/18/26.
//

import Foundation

private func normalizedDiscussionURLKey(_ value: String?) -> String? {
    guard let value else { return nil }
    var cleaned = value.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    while cleaned.hasSuffix("/") {
        cleaned.removeLast()
    }
    return cleaned.isEmpty ? nil : cleaned
}

struct ContentDiscussion: Codable {
    let contentId: Int
    let status: String
    let mode: String
    let platform: String?
    let sourceURL: String?
    let discussionURL: String?
    let fetchedAt: String?
    let errorMessage: String?
    let comments: [DiscussionComment]
    let discussionGroups: [DiscussionGroup]
    let links: [DiscussionLink]
    let summary: DiscussionSummary?
    let commentCount: Int?
    let stats: [String: AnyCodable]

    init(
        contentId: Int,
        status: String,
        mode: String,
        platform: String?,
        sourceURL: String?,
        discussionURL: String?,
        fetchedAt: String?,
        errorMessage: String?,
        comments: [DiscussionComment],
        discussionGroups: [DiscussionGroup],
        links: [DiscussionLink],
        summary: DiscussionSummary? = nil,
        commentCount: Int? = nil,
        stats: [String: AnyCodable]
    ) {
        self.contentId = contentId
        self.status = status
        self.mode = mode
        self.platform = platform
        self.sourceURL = sourceURL
        self.discussionURL = discussionURL
        self.fetchedAt = fetchedAt
        self.errorMessage = errorMessage
        self.comments = comments
        self.discussionGroups = discussionGroups
        self.links = links
        self.summary = summary
        self.commentCount = commentCount
        self.stats = stats
    }

    enum CodingKeys: String, CodingKey {
        case contentId = "content_id"
        case status
        case mode
        case platform
        case sourceURL = "source_url"
        case discussionURL = "discussion_url"
        case fetchedAt = "fetched_at"
        case errorMessage = "error_message"
        case comments
        case discussionGroups = "discussion_groups"
        case links
        case summary
        case commentCount = "comment_count"
        case stats
    }

    var hasRenderableContent: Bool {
        if mode == "comments" {
            return summary != nil || !comments.isEmpty || !links.isEmpty
        }
        if mode == "discussion_list" {
            return !discussionGroups.isEmpty || !links.isEmpty
        }
        return false
    }

    var linksOutsideSummary: [DiscussionLink] {
        guard let summary else { return links }

        let summaryLinkKeys = Set(
            summary.notableLinks.compactMap { normalizedDiscussionURLKey($0.url) }
        )
        guard !summaryLinkKeys.isEmpty else { return links }

        return links.filter { link in
            guard let key = normalizedDiscussionURLKey(link.url) else { return true }
            return !summaryLinkKeys.contains(key)
        }
    }

    var shouldAutoRefresh: Bool {
        if status == "not_ready" {
            return true
        }
        if status == "failed" {
            return false
        }
        if mode == "comments" {
            return summary == nil && comments.isEmpty && links.isEmpty
        }
        return false
    }

    var unavailableMessage: String {
        if let errorMessage = normalizedMessage(errorMessage) {
            return errorMessage
        }

        switch status {
        case "not_ready":
            return "Comments are still being prepared for this story."
        case "failed":
            return "Comments could not be loaded in the app right now."
        default:
            if discussionURL != nil || sourceURL != nil {
                return "This story has a discussion link, but there is no in-app discussion payload yet."
            }
            return "No discussion is available for this story."
        }
    }

    private func normalizedMessage(_ value: String?) -> String? {
        guard let value else { return nil }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }
}

struct DiscussionSummary: Codable {
    let overview: String
    let topics: [DiscussionSummaryTopic]
    let notableLinks: [DiscussionSummaryLink]
    let representativeComments: [DiscussionSummaryComment]
    let externalDiscussionURL: String?
    let generatedAt: String?

    enum CodingKeys: String, CodingKey {
        case overview
        case topics
        case notableLinks = "notable_links"
        case representativeComments = "representative_comments"
        case externalDiscussionURL = "external_discussion_url"
        case generatedAt = "generated_at"
    }
}

struct DiscussionSummaryTopic: Codable, Identifiable {
    let title: String
    let summary: String
    let stance: String?

    var id: String { "\(title)-\(summary)" }
}

struct DiscussionSummaryLink: Codable, Identifiable {
    let url: String
    let title: String?
    let reason: String?
    let sourceCommentID: String?

    enum CodingKeys: String, CodingKey {
        case url
        case title
        case reason
        case sourceCommentID = "source_comment_id"
    }

    var id: String { url }
}

struct DiscussionSummaryComment: Codable, Identifiable {
    let commentID: String?
    let author: String?
    let text: String
    let reason: String?

    enum CodingKeys: String, CodingKey {
        case commentID = "comment_id"
        case author
        case text
        case reason
    }

    var id: String { commentID ?? "\(author ?? "unknown")-\(text)" }
}

struct DiscussionComment: Codable, Identifiable {
    let commentID: String
    let parentID: String?
    let author: String?
    let text: String
    let compactText: String?
    let depth: Int
    let createdAt: String?
    let sourceURL: String?

    enum CodingKeys: String, CodingKey {
        case commentID = "comment_id"
        case parentID = "parent_id"
        case author
        case text
        case compactText = "compact_text"
        case depth
        case createdAt = "created_at"
        case sourceURL = "source_url"
    }

    var id: String { commentID }
}

struct DiscussionGroup: Codable, Identifiable {
    let label: String
    let items: [DiscussionItem]

    var id: String { label }
}

struct DiscussionItem: Codable, Identifiable {
    let title: String
    let url: String

    var id: String { url }
}

struct DiscussionLink: Codable, Identifiable {
    let url: String
    let source: String
    let commentID: String?
    let groupLabel: String?
    let title: String?

    enum CodingKeys: String, CodingKey {
        case url
        case source
        case commentID = "comment_id"
        case groupLabel = "group_label"
        case title
    }

    var id: String { url }
}
