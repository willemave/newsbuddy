#if DEBUG
import SwiftUI

/// Exercises the real merge and saved-row views without generating paid artifacts.
struct E2EKnowledgeSourceActivityVisualState: View {
    @State private var deckStatus: LearningDeckRunStatus = .generating
    @State private var chatIsAnswering = true
    @State private var hasDeck = true
    @State private var hasChat = true
    @State private var opened = ""

    private static let now = AppClock.now

    private var saved: [ContentSummary] {
        [
            source(id: 901, title: "Finetuning with Sampling: SFT Lessons", summary: "The paper proposes reshaping expert traces to be more useful."),
            source(id: 902, title: "Jev’s case: useful bounded decisions", summary: "Four enterprise tests suggest Jev’s strongest fit is fast decisions.")
        ]
    }

    private var chats: [ChatSessionSummary] {
        guard hasChat else { return [] }
        return [ChatSessionSummary(
            id: 904, contentId: 902, title: "Jev’s case: useful bounded decisions",
            sessionType: "knowledge_chat", topic: nil, llmProvider: "openai", llmModel: "openai:gpt-5.5",
            createdAt: Self.now, updatedAt: nil, lastMessageAt: Self.now,
            articleTitle: nil, articleUrl: nil, articleSummary: nil, articleSource: nil,
            hasPendingMessage: chatIsAnswering, lastMessagePreview: "Key takeaways", lastMessageRole: "assistant"
        )]
    }

    private var decks: [LearningDeck] {
        guard hasDeck else { return [] }
        return [LearningDeck(
            id: 903, title: "Finetuning with Sampling: SFT Lessons", sourceKind: .content,
            sourceURL: nil, sourceContentId: 901, sourceTitle: nil, sourceMetadata: [:],
            status: deckStatus, shareEnabled: false, viewerAvailable: deckStatus == .completed,
            latestSuccessfulRunId: deckStatus == .completed ? 905 : nil,
            latestRun: LearningDeckRun(
                id: 905, status: deckStatus, interestsPrompt: nil, timeline: [], errorMessage: nil,
                startedAt: Self.now, completedAt: nil, createdAt: Self.now, updatedAt: Self.now
            ), createdAt: Self.now, updatedAt: Self.now
        )]
    }

    var body: some View {
        let items = KnowledgeTimelineItem.merged(saved: saved, chats: chats, decks: decks, narrations: [])
        let derivatives = KnowledgeSourceDerivatives.index(chats: chats, decks: decks, narrations: [])
        List {
            EditorialMastheadHeader(title: "Knowledge")
                .appListRow()
            KnowledgeDayDelimiter(title: "TODAY")
                .appListRow()
            ForEach(items) { item in
                row(item, derivatives: derivatives)
            }
            Button("Finish activity") {
                deckStatus = .completed
                chatIsAnswering = false
            }
            .accessibilityIdentifier("knowledge.source_activity.finish")
            Button("Fail deck") {
                deckStatus = .failed
                chatIsAnswering = false
            }
            .accessibilityIdentifier("knowledge.source_activity.fail")
            Button("Start activity") {
                hasDeck = true
                hasChat = true
                deckStatus = .generating
                chatIsAnswering = true
            }
            .accessibilityIdentifier("knowledge.source_activity.start")
            if !opened.isEmpty { Text(opened).accessibilityIdentifier("knowledge.source_activity.opened") }
        }
        .listStyle(.plain)
        .scrollContentBackground(.hidden)
        .background(Color.surfacePrimary)
    }

    @ViewBuilder
    private func row(
        _ item: KnowledgeTimelineItem,
        derivatives: [KnowledgeSource: KnowledgeSourceDerivatives]
    ) -> some View {
        switch item {
        case .saved(let entry):
            KnowledgeSavedContentButton(
                entry: entry,
                accessibilityIdentifier: item.id,
                onOpen: { opened = "Saved source opened" },
                onRefresh: {}, onReprocess: { false }, onRemove: {},
                derivatives: derivatives[KnowledgeSource(entry.content)] ?? []
            )
            .contextMenu {
                KnowledgeSourceActivityMenu(entry: entry, busyDeckIDs: [], perform: perform)
            }
            .appListRow()
            .knowledgeSourceSwipeActions(
                for: entry.content, onRemove: {},
                perform: { action in
                    switch action {
                    case .chat: opened = "Chat opened"
                    case .deck: opened = "Deck opened"
                    case .listen: break
                    }
                }
            )
        case .chat(let session, let preview):
            Button { perform(.openChat(session)) } label: {
                KnowledgeChatRow(session: session, activityDate: item.activityDate, preview: preview)
            }
            .accessibilityIdentifier(item.id)
            .appListRow()
        case .deck(let deck):
            Button { perform(.openDeck(deck)) } label: {
                KnowledgeDeckTimelineRow(deck: deck, activityDate: item.activityDate)
            }
            .accessibilityIdentifier(item.id)
            .appListRow()
        case .narration(let episode):
            KnowledgeNarrationRow(
                episode: episode, activityDate: item.activityDate,
                subtitle: episode.subtitle ?? "", isPlaying: false
            )
            .appListRow()
        }
    }

    private func perform(_ action: KnowledgeRelatedAction) {
        switch action {
        case .openChat: opened = "Chat opened"
        case .deleteChat: hasChat = false
        case .openDeck: opened = "Deck opened"
        case .regenerateDeck: deckStatus = .generating
        case .deleteDeck: hasDeck = false
        }
    }

    private func source(id: Int, title: String, summary: String) -> ContentSummary {
        ContentSummary(
            id: id, contentType: .article, url: "https://example.invalid/\(id)", title: title,
            source: "Example", platform: nil, status: .completed, shortSummary: summary,
            createdAt: ServerDate.format(Self.now.addingTimeInterval(-24 * 60 * 60)), processedAt: nil,
            isRead: false, isSavedToKnowledge: true,
            knowledgeSavedAt: ServerDate.format(Self.now.addingTimeInterval(-5 * 60 * 60))
        )
    }
}
#endif
