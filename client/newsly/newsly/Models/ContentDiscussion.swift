//
//  ContentDiscussion.swift
//  newsly
//

import Foundation

typealias ContentDiscussion = APIContentDiscussionResponse
typealias DiscussionSummary = APIDiscussionSummaryResponse
typealias DiscussionSummaryTopic = APIDiscussionSummaryTopicResponse
typealias DiscussionSummaryLink = APIDiscussionSummaryLinkResponse
typealias DiscussionSummaryComment = APIDiscussionSummaryCommentResponse
typealias DiscussionComment = APIDiscussionCommentResponse
typealias DiscussionGroup = APIDiscussionGroupResponse
typealias DiscussionItem = APIDiscussionItemResponse
typealias DiscussionLink = APIDiscussionLinkResponse

extension APIDiscussionSummaryTopicResponse: Identifiable {
    var id: String { "\(title)-\(summary)" }
}

extension APIDiscussionSummaryLinkResponse: Identifiable {
    var id: String { url }
}

extension APIDiscussionSummaryCommentResponse: Identifiable {
    var id: String { commentId ?? "\(author ?? "unknown")-\(text)" }
}

extension APIDiscussionCommentResponse: Identifiable {
    var id: String { commentId }
}

extension APIDiscussionGroupResponse: Identifiable {
    var id: String { label }
}

extension APIDiscussionItemResponse: Identifiable {
    var id: String { url }
}

extension APIDiscussionLinkResponse: Identifiable {
    var id: String { url }
}
