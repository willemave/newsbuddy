//
//  ChatMessageList.swift
//  newsly
//

import SwiftUI

struct ChatMessageList: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    let timeline: [ChatTimelineItem]
    let hasMessages: Bool
    let isLoading: Bool
    let loadErrorMessage: String?
    let errorMessage: String?
    let isStartingCouncil: Bool
    let isSending: Bool
    let thinkingStartedAt: Date?
    let latestProcessSummary: String?
    let session: ChatSessionSummary?
    let scrollToBottomRequest: Int
    let retryingCouncilChildSessionId: Int?
    let onOpenCouncilSettings: () -> Void
    let onDismissError: () -> Void
    let onRetryLoad: () -> Void
    let onRetrySend: (String) -> Void
    let onRetryCouncilCandidate: (CouncilCandidate) -> Void
    let onDigDeeper: (String) -> Void
    let onShare: (String) -> Void

    @State private var isNearBottom = true
    @State private var hasNewerContentBelow = false
    @State private var hasAnchoredInitialScroll = false
    @State private var feedOptionActionModel = AssistantFeedOptionActionModel()

    private static let thinkingBubbleID = "chat.thinkingBubble"

    private var messageAnimation: Animation {
        AppMotion.respectingReduceMotion(reduceMotion, AppMotion.subtle)
    }

    private var messageInsertionTransition: AnyTransition {
        if reduceMotion {
            return .opacity
        } else {
            return .opacity.combined(with: .move(edge: .bottom))
        }
    }

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 12) {
                    if let errorMessage {
                        ChatErrorBanner(
                            error: errorMessage,
                            onAddExperts: onOpenCouncilSettings,
                            onDismiss: onDismissError
                        )
                    }

                    if isLoading && !hasMessages {
                        ChatLoadingView()
                            .frame(maxWidth: .infinity)
                            .padding(.top, 40)
                    } else if let loadErrorMessage, !hasMessages {
                        ChatLoadErrorState(
                            error: loadErrorMessage,
                            onRetry: onRetryLoad
                        )
                        .padding()
                    } else if !hasMessages {
                        emptyTimelineState
                            .padding(.top, 40)
                    } else {
                        if let session, let articleTitle = session.articleTitle {
                            ArticlePreviewCard(
                                title: articleTitle,
                                source: session.articleSource,
                                summary: session.articleSummary,
                                url: session.articleUrl
                            )
                        }

                        ForEach(timeline) { item in
                            MessageRow(
                                item: item,
                                retryingCouncilChildSessionId: retryingCouncilChildSessionId,
                                feedOptionActionModel: feedOptionActionModel,
                                onRetrySend: onRetrySend,
                                onRetryCouncilCandidate: onRetryCouncilCandidate,
                                onDigDeeper: onDigDeeper,
                                onShare: onShare
                            )
                            .id(item.id)
                            .transition(messageInsertionTransition)
                        }
                        .animation(messageAnimation, value: timeline.last?.id)

                        Group {
                            if isSending {
                                ThinkingBubbleView(
                                    startDate: thinkingStartedAt,
                                    statusText: latestProcessSummary
                                )
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .transition(messageInsertionTransition)
                                .id(Self.thinkingBubbleID)
                            }
                        }
                        .animation(messageAnimation, value: isSending)
                    }
                }
                .padding(.horizontal, Spacing.appHorizontalMargin)
                .padding(.vertical, 10)
            }
            .defaultScrollAnchor(.bottom)
            .contentMargins(.bottom, 12, for: .scrollContent)
            .onScrollGeometryChange(for: Bool.self) { geometry in
                let distanceFromBottom =
                    geometry.contentSize.height
                    - geometry.visibleRect.maxY
                    + geometry.contentInsets.bottom
                return distanceFromBottom < 48
            } action: { _, isNearBottom in
                handleBottomProximityChange(isNearBottom)
            }
            .onChange(of: timeline.last?.id) { _, newId in
                handleTimelineChange(newId, proxy: proxy)
            }
            .onChange(of: isSending) { wasSending, sending in
                handleSendingChange(from: wasSending, to: sending, proxy: proxy)
            }
            .onChange(of: scrollToBottomRequest) { _, _ in
                scrollToLatest(proxy: proxy)
            }
            .overlay(alignment: .bottom) {
                jumpToLatestOverlay(proxy: proxy)
            }
        }
    }

    private func handleBottomProximityChange(_ newValue: Bool) {
        if isNearBottom != newValue {
            isNearBottom = newValue
        }
        if newValue, hasNewerContentBelow {
            hasNewerContentBelow = false
        }
    }

    private func handleTimelineChange(_ newId: ChatTimelineID?, proxy: ScrollViewProxy) {
        guard let newId else { return }
        if !hasAnchoredInitialScroll {
            hasAnchoredInitialScroll = true
            proxy.scrollTo(newId, anchor: .bottom)
            return
        }
        guard isNearBottom || isLocalUserInsertion(newId) || isSending else {
            hasNewerContentBelow = true
            return
        }
        scrollToBottom(newId, proxy: proxy)
    }

    private func handleSendingChange(
        from wasSending: Bool,
        to sending: Bool,
        proxy: ScrollViewProxy
    ) {
        if sending {
            guard isNearBottom else { return }
            scrollToBottom(Self.thinkingBubbleID, proxy: proxy)
            return
        }

        guard wasSending else { return }
        scrollToLatest(proxy: proxy)
    }

    private func scrollToLatest(proxy: ScrollViewProxy) {
        if let anchorId = timeline.last?.id {
            scrollToBottom(anchorId, proxy: proxy)
        }
        hasNewerContentBelow = false
    }

    private func scrollToBottom<ID: Hashable>(_ target: ID, proxy: ScrollViewProxy) {
        withAnimation(messageAnimation) {
            proxy.scrollTo(target, anchor: .bottom)
        }
    }

    private func isLocalUserInsertion(_ id: ChatTimelineID) -> Bool {
        guard case .local = id else { return false }
        return timeline.last?.id == id && timeline.last?.message.isUser == true
    }

    @ViewBuilder
    private var emptyTimelineState: some View {
        if isStartingCouncil {
            VStack(alignment: .leading, spacing: 18) {
                if let session, let articleTitle = session.articleTitle {
                    ArticlePreviewCard(
                        title: articleTitle,
                        source: session.articleSource,
                        summary: session.articleSummary,
                        url: session.articleUrl
                    )
                }

                ThinkingBubbleView(
                    startDate: thinkingStartedAt,
                    statusText: "Gathering council perspectives"
                )
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        } else if isSending {
            InitialSuggestionsLoadingView()
                .frame(maxWidth: .infinity)
        } else if let session, let articleTitle = session.articleTitle {
            ArticlePreviewCard(
                title: articleTitle,
                source: session.articleSource,
                summary: session.articleSummary,
                url: session.articleUrl
            )
        } else {
            ChatEmptyState(topic: session?.topic)
        }
    }

    @ViewBuilder
    private func jumpToLatestOverlay(proxy: ScrollViewProxy) -> some View {
        if hasNewerContentBelow {
            Button {
                scrollToLatest(proxy: proxy)
            } label: {
                Label("Jump to latest", systemImage: "arrow.down")
                    .font(.terracottaBodySmall.weight(.semibold))
                    .foregroundStyle(Color.chatAccent)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 8)
                    .background(Color.surfacePrimary.opacity(0.96), in: Capsule())
                    .overlay(
                        Capsule()
                            .stroke(Color.chatAccent.opacity(0.24), lineWidth: 1)
                    )
                    .appShadow(.subtle)
            }
            .buttonStyle(.plain)
            .padding(.bottom, 10)
            .transition(.opacity)
            .animation(messageAnimation, value: hasNewerContentBelow)
        }
    }

}

private struct ChatLoadErrorState: View {
    let error: String
    let onRetry: () -> Void

    var body: some View {
        VStack(spacing: 12) {
            Image(systemName: "exclamationmark.triangle")
                .font(.appSymbol(size: 36))
                .foregroundStyle(Color.statusDestructive.opacity(0.8))

            Text(error)
                .font(.appSubheadline)
                .foregroundStyle(Color.onSurfaceSecondary)
                .multilineTextAlignment(.center)

            Button(action: onRetry) {
                Label("Retry", systemImage: "arrow.clockwise")
                    .font(.terracottaBodySmall.weight(.semibold))
                    .foregroundStyle(Color.chatAccent)
                    .padding(.horizontal, 16)
                    .padding(.vertical, 10)
                    .background(Color.chatAccent.opacity(0.12), in: Capsule())
                    .overlay(Capsule().stroke(Color.chatAccent.opacity(0.24), lineWidth: 1))
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("knowledge.chat_load_retry")
        }
    }
}

#if DEBUG
#Preview("Chat Message List") {
    ChatMessageList(
        timeline: ChatPreviewFixtures.timeline,
        hasMessages: true,
        isLoading: false,
        loadErrorMessage: nil,
        errorMessage: nil,
        isStartingCouncil: false,
        isSending: true,
        thinkingStartedAt: Date(timeIntervalSinceNow: -42),
        latestProcessSummary: "Drafting a grounded response",
        session: ChatPreviewFixtures.session,
        scrollToBottomRequest: 0,
        retryingCouncilChildSessionId: nil,
        onOpenCouncilSettings: {},
        onDismissError: {},
        onRetryLoad: {},
        onRetrySend: { _ in },
        onRetryCouncilCandidate: { _ in },
        onDigDeeper: { _ in },
        onShare: { _ in }
    )
}
#endif
