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

    // Decodes through the generated wire model so `summary` picks up the typed,
    // server-validated shape (APIDiscussionSummaryResponse) instead of hand-parsing
    // JSON. `stats` stays AnyCodable: its keys vary by discussion platform/mode and
    // are not consumed by the app (see ContentDiscussionResponse.stats on the
    // backend, an intentional escape hatch).
    init(from decoder: Decoder) throws {
        let response = try APIContentDiscussionResponse(from: decoder)
        contentId = response.contentId
        status = response.status
        mode = response.mode.rawValue
        platform = response.platform
        sourceURL = response.sourceUrl
        discussionURL = response.discussionUrl
        fetchedAt = response.fetchedAt
        errorMessage = response.errorMessage
        comments = response.comments.map(DiscussionComment.init(api:))
        discussionGroups = response.discussionGroups.map(DiscussionGroup.init(api:))
        links = response.links.map(DiscussionLink.init(api:))
        summary = response.summary.map(DiscussionSummary.init(api:))
        commentCount = response.commentCount
        stats = response.stats
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(contentId, forKey: .contentId)
        try container.encode(status, forKey: .status)
        try container.encode(mode, forKey: .mode)
        try container.encodeIfPresent(platform, forKey: .platform)
        try container.encodeIfPresent(sourceURL, forKey: .sourceURL)
        try container.encodeIfPresent(discussionURL, forKey: .discussionURL)
        try container.encodeIfPresent(fetchedAt, forKey: .fetchedAt)
        try container.encodeIfPresent(errorMessage, forKey: .errorMessage)
        try container.encode(comments, forKey: .comments)
        try container.encode(discussionGroups, forKey: .discussionGroups)
        try container.encode(links, forKey: .links)
        try container.encodeIfPresent(summary, forKey: .summary)
        try container.encodeIfPresent(commentCount, forKey: .commentCount)
        try container.encode(stats, forKey: .stats)
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

// Domain type. Field names mirror APIDiscussionSummaryResponse; `init(api:)` maps
// from the generated wire model (see ContentDiscussion.init(from:)).
struct DiscussionSummary: Codable {
    let overview: String
    let topics: [DiscussionSummaryTopic]
    let notableLinks: [DiscussionSummaryLink]
    let representativeComments: [DiscussionSummaryComment]
    let externalDiscussionURL: String?
    let generatedAt: String?

    init(
        overview: String,
        topics: [DiscussionSummaryTopic],
        notableLinks: [DiscussionSummaryLink],
        representativeComments: [DiscussionSummaryComment],
        externalDiscussionURL: String?,
        generatedAt: String?
    ) {
        self.overview = overview
        self.topics = topics
        self.notableLinks = notableLinks
        self.representativeComments = representativeComments
        self.externalDiscussionURL = externalDiscussionURL
        self.generatedAt = generatedAt
    }

    init(api response: APIDiscussionSummaryResponse) {
        overview = response.overview
        topics = response.topics.map(DiscussionSummaryTopic.init(api:))
        notableLinks = response.notableLinks.map(DiscussionSummaryLink.init(api:))
        representativeComments = response.representativeComments.map(DiscussionSummaryComment.init(api:))
        externalDiscussionURL = response.externalDiscussionUrl
        generatedAt = response.generatedAt
    }

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

    init(title: String, summary: String, stance: String?) {
        self.title = title
        self.summary = summary
        self.stance = stance
    }

    init(api response: APIDiscussionSummaryTopicResponse) {
        title = response.title
        summary = response.summary
        stance = response.stance
    }
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

    init(url: String, title: String?, reason: String?, sourceCommentID: String?) {
        self.url = url
        self.title = title
        self.reason = reason
        self.sourceCommentID = sourceCommentID
    }

    init(api response: APIDiscussionSummaryLinkResponse) {
        url = response.url
        title = response.title
        reason = response.reason
        sourceCommentID = response.sourceCommentId
    }
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

    init(commentID: String?, author: String?, text: String, reason: String?) {
        self.commentID = commentID
        self.author = author
        self.text = text
        self.reason = reason
    }

    init(api response: APIDiscussionSummaryCommentResponse) {
        commentID = response.commentId
        author = response.author
        text = response.text
        reason = response.reason
    }
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

    init(
        commentID: String,
        parentID: String?,
        author: String?,
        text: String,
        compactText: String?,
        depth: Int,
        createdAt: String?,
        sourceURL: String?
    ) {
        self.commentID = commentID
        self.parentID = parentID
        self.author = author
        self.text = text
        self.compactText = compactText
        self.depth = depth
        self.createdAt = createdAt
        self.sourceURL = sourceURL
    }

    init(api response: APIDiscussionCommentResponse) {
        commentID = response.commentId
        parentID = response.parentId
        author = response.author
        text = response.text
        compactText = response.compactText
        depth = response.depth
        createdAt = response.createdAt
        sourceURL = response.sourceUrl
    }
}

struct DiscussionGroup: Codable, Identifiable {
    let label: String
    let items: [DiscussionItem]

    var id: String { label }

    init(label: String, items: [DiscussionItem]) {
        self.label = label
        self.items = items
    }

    init(api response: APIDiscussionGroupResponse) {
        label = response.label
        items = response.items.map(DiscussionItem.init(api:))
    }
}

struct DiscussionItem: Codable, Identifiable {
    let title: String
    let url: String

    var id: String { url }

    init(title: String, url: String) {
        self.title = title
        self.url = url
    }

    init(api response: APIDiscussionItemResponse) {
        title = response.title
        url = response.url
    }
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

    init(url: String, source: String, commentID: String?, groupLabel: String?, title: String?) {
        self.url = url
        self.source = source
        self.commentID = commentID
        self.groupLabel = groupLabel
        self.title = title
    }

    init(api response: APIDiscussionLinkResponse) {
        url = response.url
        source = response.source
        commentID = response.commentId
        groupLabel = response.groupLabel
        title = response.title
    }
}
