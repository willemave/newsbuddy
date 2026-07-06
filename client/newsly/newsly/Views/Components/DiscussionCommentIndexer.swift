//
//  DiscussionCommentIndexer.swift
//  newsly
//

import Foundation

struct DiscussionCommentIndex {
    let orderedComments: [DiscussionComment]
    let commentsByID: [String: DiscussionComment]
    let descendantCountByID: [String: Int]
}

enum DiscussionCommentIndexer {
    static func build(from comments: [DiscussionComment]) -> DiscussionCommentIndex {
        guard !comments.isEmpty else {
            return DiscussionCommentIndex(orderedComments: [], commentsByID: [:], descendantCountByID: [:])
        }

        var commentsByID: [String: DiscussionComment] = [:]
        var childrenByParentID: [String: [DiscussionComment]] = [:]
        var roots: [DiscussionComment] = []

        for comment in comments {
            commentsByID[comment.commentID] = comment
            if let parentID = comment.parentID {
                childrenByParentID[parentID, default: []].append(comment)
            } else {
                roots.append(comment)
            }
        }

        if roots.isEmpty {
            roots = comments.filter { $0.depth == 0 }
        }
        if roots.isEmpty {
            roots = comments
        }

        var orderedComments: [DiscussionComment] = []
        var stack = Array(roots.reversed())
        while let current = stack.popLast() {
            orderedComments.append(current)
            if let children = childrenByParentID[current.commentID] {
                for child in children.reversed() {
                    stack.append(child)
                }
            }
        }

        var descendantCountByID: [String: Int] = [:]

        func computeDescendantCount(for commentID: String) -> Int {
            if let cached = descendantCountByID[commentID] {
                return cached
            }

            let children = childrenByParentID[commentID] ?? []
            let total = children.reduce(0) { partialResult, child in
                partialResult + 1 + computeDescendantCount(for: child.commentID)
            }
            descendantCountByID[commentID] = total
            return total
        }

        for comment in comments {
            _ = computeDescendantCount(for: comment.commentID)
        }

        return DiscussionCommentIndex(
            orderedComments: orderedComments,
            commentsByID: commentsByID,
            descendantCountByID: descendantCountByID
        )
    }

    static func isHiddenByCollapse(
        _ comment: DiscussionComment,
        collapsedCommentIDs: Set<String>,
        commentsByID: [String: DiscussionComment]
    ) -> Bool {
        guard !collapsedCommentIDs.isEmpty else { return false }
        var current = comment
        while let parentID = current.parentID, let parent = commentsByID[parentID] {
            if collapsedCommentIDs.contains(parent.commentID) {
                return true
            }
            current = parent
        }
        return false
    }
}
