//
//  ChatTimelineReconcilerTests.swift
//  newslyTests
//

import XCTest
@testable import newsly

final class ChatTimelineReconcilerTests: XCTestCase {
    func testStatusAndDetailAssistantMessagesUseSameServerIdentity() {
        let displayKey = "server|42|assistant|message"
        let statusMessage = message(
            id: 1_000_000_042,
            displayKey: displayKey,
            role: .assistant,
            timestamp: "2026-04-01T10:00:02Z",
            content: "Status endpoint answer"
        )
        let detailMessage = message(
            id: 2,
            sourceMessageId: 42,
            displayKey: displayKey,
            role: .assistant,
            timestamp: "2026-04-01T10:00:02Z",
            content: "Session detail answer"
        )

        XCTAssertEqual(ChatTimelineID.server(for: statusMessage), ChatTimelineID.server(for: detailMessage))
    }

    func testPendingUserRowKeepsLocalIdentityAfterServerEcho() {
        let localId = UUID(uuidString: "11111111-1111-1111-1111-111111111111")!
        let pending = PendingSend(
            localId: localId,
            text: "What matters here?",
            messageId: 42,
            createdAt: "2026-04-01T10:00:00Z"
        )
        let localItem = ChatTimelineItem(
            id: .local(localId),
            message: message(
                id: 1,
                sourceMessageId: nil,
                role: .user,
                timestamp: pending.createdAt,
                content: pending.text,
                status: .processing
            ),
            pendingMessageId: nil,
            retryText: pending.text
        )
        let detail = detail(messages: [
            message(
                id: 1,
                sourceMessageId: 42,
                role: .user,
                timestamp: pending.createdAt,
                content: pending.text
            )
        ])

        var aliases: [ChatTimelineID: UUID] = [:]
        let result = ChatTimelineReconciler().reconcile(
            current: [localItem],
            detail: detail,
            pendingSends: [localId: pending],
            localIdentityAliases: &aliases
        )

        XCTAssertEqual(result.map(\.id), [.local(localId)])
        XCTAssertEqual(result.first?.message.sourceMessageId, 42)
        XCTAssertEqual(aliases[.server(sourceMessageId: 42, role: .user, displayType: .message)], localId)
    }

    func testLocalIdentityAliasPersistsAfterPendingSendCompletes() {
        let localId = UUID(uuidString: "22222222-2222-2222-2222-222222222222")!
        let serverId = ChatTimelineID.server(sourceMessageId: 42, role: .user, displayType: .message)
        var aliases: [ChatTimelineID: UUID] = [serverId: localId]
        let detail = detail(messages: [
            message(
                id: 1,
                sourceMessageId: 42,
                role: .user,
                timestamp: "2026-04-01T10:00:00Z",
                content: "What matters here?"
            )
        ])

        let result = ChatTimelineReconciler().reconcile(
            current: [],
            detail: detail,
            pendingSends: [:],
            localIdentityAliases: &aliases
        )

        XCTAssertEqual(result.map(\.id), [.local(localId)])
    }

    func testReconcileSuppressesDuplicateServerRowsForSamePendingSend() {
        let localId = UUID(uuidString: "33333333-3333-3333-3333-333333333333")!
        let pending = PendingSend(
            localId: localId,
            text: "Compare the views",
            messageId: 99,
            createdAt: "2026-04-01T10:00:00Z"
        )
        let detail = detail(messages: [
            message(
                id: 1,
                sourceMessageId: 99,
                role: .user,
                timestamp: "2026-04-01T10:00:00Z",
                content: "Compare the views"
            ),
            message(
                id: 1_000_000_099,
                sourceMessageId: 99,
                role: .assistant,
                timestamp: "2026-04-01T10:00:01Z",
                content: "Working on it",
                status: .processing
            )
        ])

        var aliases: [ChatTimelineID: UUID] = [:]
        let result = ChatTimelineReconciler().reconcile(
            current: [],
            detail: detail,
            pendingSends: [localId: pending],
            localIdentityAliases: &aliases
        )

        XCTAssertEqual(result.count, 2)
        XCTAssertEqual(result[0].id, .local(localId))
        XCTAssertEqual(result[1].id, .server(sourceMessageId: 99, role: .assistant, displayType: .message))
    }

    func testReconcilePreservesProcessSummaryAndCouncilOrdering() {
        let councilCandidates = [
            CouncilCandidate(
                personaId: "skeptic",
                personaName: "Skeptic",
                childSessionId: 202,
                content: "Skeptic branch",
                status: "completed",
                order: 1
            ),
            CouncilCandidate(
                personaId: "analyst",
                personaName: "Analyst",
                childSessionId: 201,
                content: "Analyst branch",
                status: "completed",
                order: 0
            )
        ]
        let detail = detail(messages: [
            message(
                id: 1,
                role: .user,
                timestamp: "2026-04-01T10:00:00Z",
                content: "Start council"
            ),
            message(
                id: 2,
                role: .tool,
                timestamp: "2026-04-01T10:00:01Z",
                content: "Thinking",
                displayType: .processSummary
            ),
            message(
                id: 3,
                role: .assistant,
                timestamp: "2026-04-01T10:00:02Z",
                content: "",
                councilCandidates: councilCandidates,
                activeCouncilChildSessionId: 201
            )
        ])

        var aliases: [ChatTimelineID: UUID] = [:]
        let result = ChatTimelineReconciler().reconcile(
            current: [],
            detail: detail,
            pendingSends: [:],
            localIdentityAliases: &aliases
        )

        XCTAssertEqual(result.map(\.message.displayType), [.message, .processSummary, .message])
        XCTAssertEqual(result.last?.message.councilCandidates.map(\.personaName), ["Skeptic", "Analyst"])
    }

    func testReconcilePreservesFailedLocalRetryRow() {
        let localId = UUID(uuidString: "44444444-4444-4444-4444-444444444444")!
        let failedItem = ChatTimelineItem(
            id: .local(localId),
            message: message(
                id: 44,
                role: .user,
                timestamp: "2026-04-01T10:00:00Z",
                content: "Try this again",
                status: .failed,
                error: "Network unavailable"
            ),
            pendingMessageId: nil,
            retryText: "Try this again"
        )

        var aliases: [ChatTimelineID: UUID] = [:]
        let result = ChatTimelineReconciler().reconcile(
            current: [failedItem],
            detail: detail(messages: []),
            pendingSends: [:],
            localIdentityAliases: &aliases
        )

        XCTAssertEqual(result, [failedItem])
    }

    func testReconcileKeepsRapidPendingSendsInOrderAndLocalIdentity() {
        let firstLocalId = UUID(uuidString: "55555555-5555-5555-5555-555555555555")!
        let secondLocalId = UUID(uuidString: "66666666-6666-6666-6666-666666666666")!
        let firstPending = PendingSend(
            localId: firstLocalId,
            text: "First",
            messageId: 101,
            createdAt: "2026-04-01T10:00:00Z"
        )
        let secondPending = PendingSend(
            localId: secondLocalId,
            text: "Second",
            messageId: 102,
            createdAt: "2026-04-01T10:00:01Z"
        )
        let detail = detail(messages: [
            message(
                id: 1,
                sourceMessageId: 101,
                role: .user,
                timestamp: firstPending.createdAt,
                content: firstPending.text
            ),
            message(
                id: 2,
                sourceMessageId: 102,
                role: .user,
                timestamp: secondPending.createdAt,
                content: secondPending.text
            )
        ])

        var aliases: [ChatTimelineID: UUID] = [:]
        let result = ChatTimelineReconciler().reconcile(
            current: [],
            detail: detail,
            pendingSends: [
                firstLocalId: firstPending,
                secondLocalId: secondPending
            ],
            localIdentityAliases: &aliases
        )

        XCTAssertEqual(result.map(\.id), [.local(firstLocalId), .local(secondLocalId)])
        XCTAssertEqual(result.map(\.message.content), ["First", "Second"])
        XCTAssertEqual(aliases[.server(sourceMessageId: 101, role: .user, displayType: .message)], firstLocalId)
        XCTAssertEqual(aliases[.server(sourceMessageId: 102, role: .user, displayType: .message)], secondLocalId)
    }

    private func detail(messages: [ChatMessage]) -> ChatSessionDetail {
        ChatSessionDetail(session: session(), messages: messages)
    }

    private func session() -> ChatSessionSummary {
        ChatSessionSummary(
            id: 7,
            contentId: nil,
            title: "Chat",
            sessionType: "knowledge_chat",
            topic: nil,
            llmProvider: "openai",
            llmModel: "openai:gpt-5.4",
            createdAt: "2026-04-01T10:00:00Z",
            updatedAt: nil,
            lastMessageAt: nil,
            articleTitle: nil,
            articleUrl: nil,
            articleSummary: nil,
            articleSource: nil,
            hasPendingMessage: false,
            isSavedToKnowledge: false,
            hasMessages: true,
            lastMessagePreview: nil,
            lastMessageRole: nil
        )
    }

    private func message(
        id: Int,
        sourceMessageId: Int? = nil,
        displayKey: String? = nil,
        role: ChatMessageRole,
        timestamp: String,
        content: String,
        displayType: ChatMessageDisplayType = .message,
        status: MessageProcessingStatus? = .completed,
        error: String? = nil,
        councilCandidates: [CouncilCandidate] = [],
        activeCouncilChildSessionId: Int? = nil
    ) -> ChatMessage {
        ChatMessage(
            id: id,
            sourceMessageId: sourceMessageId,
            displayKey: displayKey,
            role: role,
            timestamp: timestamp,
            content: content,
            displayType: displayType,
            status: status,
            error: error,
            councilCandidates: councilCandidates,
            activeCouncilChildSessionId: activeCouncilChildSessionId
        )
    }
}
