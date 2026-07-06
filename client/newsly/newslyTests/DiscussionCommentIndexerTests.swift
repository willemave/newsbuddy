import XCTest
@testable import newsly

final class DiscussionCommentIndexerTests: XCTestCase {
    func testBuildOrdersCommentsDepthFirstAndCountsDescendants() {
        let child = Self.comment(id: "c2", parentID: "c1", depth: 1)
        let root = Self.comment(id: "c1", depth: 0)
        let grandchild = Self.comment(id: "c3", parentID: "c2", depth: 2)
        let sibling = Self.comment(id: "c4", depth: 0)

        let index = DiscussionCommentIndexer.build(from: [child, root, grandchild, sibling])

        XCTAssertEqual(index.orderedComments.map(\.commentID), ["c1", "c2", "c3", "c4"])
        XCTAssertEqual(index.commentsByID["c2"]?.parentID, "c1")
        XCTAssertEqual(index.descendantCountByID["c1"], 2)
        XCTAssertEqual(index.descendantCountByID["c2"], 1)
        XCTAssertEqual(index.descendantCountByID["c3"], 0)
        XCTAssertEqual(index.descendantCountByID["c4"], 0)
    }

    func testIsHiddenByCollapseOnlyHidesDescendantsOfCollapsedComments() {
        let root = Self.comment(id: "c1", depth: 0)
        let child = Self.comment(id: "c2", parentID: "c1", depth: 1)
        let grandchild = Self.comment(id: "c3", parentID: "c2", depth: 2)
        let sibling = Self.comment(id: "c4", depth: 0)
        let index = DiscussionCommentIndexer.build(from: [root, child, grandchild, sibling])

        XCTAssertFalse(
            DiscussionCommentIndexer.isHiddenByCollapse(
                root,
                collapsedCommentIDs: ["c1"],
                commentsByID: index.commentsByID
            )
        )
        XCTAssertTrue(
            DiscussionCommentIndexer.isHiddenByCollapse(
                child,
                collapsedCommentIDs: ["c1"],
                commentsByID: index.commentsByID
            )
        )
        XCTAssertTrue(
            DiscussionCommentIndexer.isHiddenByCollapse(
                grandchild,
                collapsedCommentIDs: ["c2"],
                commentsByID: index.commentsByID
            )
        )
        XCTAssertFalse(
            DiscussionCommentIndexer.isHiddenByCollapse(
                sibling,
                collapsedCommentIDs: ["c1"],
                commentsByID: index.commentsByID
            )
        )
    }

    private static func comment(
        id: String,
        parentID: String? = nil,
        depth: Int
    ) -> DiscussionComment {
        DiscussionComment(
            commentID: id,
            parentID: parentID,
            author: "Reader",
            text: "Comment \(id)",
            compactText: nil,
            depth: depth,
            createdAt: nil,
            sourceURL: nil
        )
    }
}
