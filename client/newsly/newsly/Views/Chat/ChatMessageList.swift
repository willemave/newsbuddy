//
//  ChatMessageList.swift
//  newsly
//

import SwiftUI

struct ChatMessageList: View {
    let timeline: [ChatTimelineItem]
    let hasMessages: Bool
    let isLoading: Bool
    let errorMessage: String?
    let isStartingCouncil: Bool
    let isSending: Bool
    let thinkingElapsedSeconds: Int
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

    @State private var scrollPosition = ScrollPosition(edge: .bottom)
    @State private var isNearBottom = true
    @State private var hasNewerContentBelow = false
    @StateObject private var feedOptionActionModel = AssistantFeedOptionActionModel()

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 12) {
                if let errorMessage, hasMessages {
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
                } else if let errorMessage, !hasMessages {
                    ChatLoadErrorState(
                        error: errorMessage,
                        onRetry: onRetryLoad
                    )
                    .padding()
                } else if !hasMessages {
                    emptyTimelineState
                        .padding(.top, 40)
                } else {
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
                    }

                    if isSending {
                        ThinkingBubbleView(
                            elapsedSeconds: thinkingElapsedSeconds,
                            statusText: latestProcessSummary
                        )
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .transition(.opacity.combined(with: .move(edge: .bottom)))
                    }
                }
            }
            .scrollTargetLayout()
            .padding()
        }
        .scrollPosition($scrollPosition)
        .defaultScrollAnchor(.bottom)
        .contentMargins(.bottom, 12, for: .scrollContent)
        .onScrollGeometryChange(for: Bool.self) { geometry in
            let distanceFromBottom =
                geometry.contentSize.height
                - geometry.visibleRect.maxY
                + geometry.contentInsets.bottom
            return distanceFromBottom < 48
        } action: { _, newValue in
            isNearBottom = newValue
            if newValue {
                hasNewerContentBelow = false
            }
        }
        .onChange(of: timeline.last?.id) { _, _ in
            if isNearBottom {
                withAnimation(.easeOut(duration: 0.2)) {
                    scrollPosition.scrollTo(edge: .bottom)
                }
            } else {
                hasNewerContentBelow = true
            }
        }
        .onChange(of: isSending) { _, sending in
            if sending, isNearBottom {
                withAnimation(.easeOut(duration: 0.2)) {
                    scrollPosition.scrollTo(edge: .bottom)
                }
            }
        }
        .onChange(of: scrollToBottomRequest) { _, _ in
            withAnimation(.easeOut(duration: 0.2)) {
                scrollPosition.scrollTo(edge: .bottom)
            }
            hasNewerContentBelow = false
        }
        .overlay(alignment: .bottom) {
            jumpToLatestOverlay
        }
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
                    elapsedSeconds: thinkingElapsedSeconds,
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
    private var jumpToLatestOverlay: some View {
        if hasNewerContentBelow {
            Button {
                withAnimation(.easeOut(duration: 0.2)) {
                    scrollPosition.scrollTo(edge: .bottom)
                }
                hasNewerContentBelow = false
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
                    .shadow(color: .black.opacity(0.08), radius: 8, y: 2)
            }
            .buttonStyle(.plain)
            .padding(.bottom, 10)
            .transition(.opacity)
            .animation(.easeOut(duration: 0.2), value: hasNewerContentBelow)
        }
    }

}

private struct ChatLoadErrorState: View {
    let error: String
    let onRetry: () -> Void

    var body: some View {
        VStack(spacing: 12) {
            Image(systemName: "exclamationmark.triangle")
                .font(.system(size: 36))
                .foregroundStyle(Color.statusDestructive.opacity(0.8))

            Text(error)
                .font(.subheadline)
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
        }
    }
}

#if DEBUG
#Preview("Chat Message List") {
    ChatMessageList(
        timeline: ChatPreviewFixtures.timeline,
        hasMessages: true,
        isLoading: false,
        errorMessage: nil,
        isStartingCouncil: false,
        isSending: true,
        thinkingElapsedSeconds: 42,
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
