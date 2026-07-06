//
//  ShortNewsQuickActionsSection.swift
//  newsly
//

import SwiftUI

struct ShortNewsQuickActionsSection: View {
    let items: [ContentSummary]
    let isPlayingAudio: Bool
    let isPreparingAudio: Bool
    let isHeaderActionInFlight: Bool
    let audioTarget: NarrationTarget?
    let playbackService: NarrationPlaybackService
    let audioErrorMessage: String?
    let quickActionErrorMessage: String?
    let activeQuickActionId: String?
    let onToggleAudio: () -> Void
    let onStartQuickAction: (ShortNewsQuickAction) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 10) {
                    Button(action: onToggleAudio) {
                        FeedActionChip(
                            title: "Audio Brief",
                            systemImage: isPlayingAudio ? "pause.fill" : "waveform",
                            isLoading: isPreparingAudio
                        )
                    }
                    .buttonStyle(EditorialCardButtonStyle())
                    .disabled(isHeaderActionInFlight)
                    .accessibilityIdentifier("short.audio.fast_reads")

                    ForEach(Self.makeQuickActions(items: items)) { action in
                        Button {
                            onStartQuickAction(action)
                        } label: {
                            FeedActionChip(
                                title: action.title,
                                systemImage: action.systemImage,
                                isLoading: activeQuickActionId == action.id
                            )
                        }
                        .buttonStyle(EditorialCardButtonStyle())
                        .disabled(isHeaderActionInFlight)
                        .accessibilityIdentifier("short.quick_action.\(action.id)")
                    }
                }
                .padding(.horizontal, Spacing.appHorizontalMargin)
            }

            if isPreparingAudio || audioTarget != nil {
                NarrationPlaybackControlRow(
                    playbackService: playbackService,
                    target: audioTarget,
                    isPreparing: isPreparingAudio,
                    cornerRadius: CornerRadius.control,
                    onTogglePlayback: onToggleAudio
                )
                .padding(.horizontal, Spacing.appHorizontalMargin)
                .transition(.opacity.combined(with: .move(edge: .top)))
            }

            if let audioErrorMessage {
                errorText(audioErrorMessage)
            }

            if let quickActionErrorMessage {
                errorText(quickActionErrorMessage)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .animation(AppMotion.subtle, value: audioErrorMessage)
        .animation(AppMotion.subtle, value: quickActionErrorMessage)
    }

    private func errorText(_ message: String) -> some View {
        Text(message)
            .font(.terracottaBodySmall)
            .foregroundStyle(Color.statusDestructive)
            .padding(.horizontal, Spacing.appHorizontalMargin)
            .transition(.opacity)
    }

    private static func makeQuickActions(items: [ContentSummary]) -> [ShortNewsQuickAction] {
        let visibleItemIds = Array(items.prefix(15).map(\.id))

        return [
            ShortNewsQuickAction(
                id: "best_unread",
                title: "Best Unread",
                systemImage: "sparkles",
                prompt: InterestingUnreadNewsAssistantAction.prompt,
                screenContext: InterestingUnreadNewsAssistantAction.screenContext(
                    screenType: "short_news_feed",
                    screenTitle: "Fast Read"
                )
            ),
            ShortNewsQuickAction(
                id: "summarize_top_15",
                title: "Summarize Top 15",
                systemImage: "text.alignleft",
                prompt: "Summarize the top 15 news items in my short news feed right now.",
                screenContext: AssistantScreenContext(
                    screenType: "short_news_feed",
                    screenTitle: "Fast Read",
                    visibleContentIds: visibleItemIds,
                    query: "top 15 news items in my short news feed",
                    note: "Summarize the most important items from the fast news feed. Prefer the in-app short news feed over web search."
                )
            ),
            ShortNewsQuickAction(
                id: "latest_news",
                title: "What's Latest",
                systemImage: "clock.arrow.trianglehead.counterclockwise.rotate.90",
                prompt: "What's the latest news in my short news feed right now?",
                screenContext: AssistantScreenContext(
                    screenType: "short_news_feed",
                    screenTitle: "Fast Read",
                    visibleContentIds: visibleItemIds,
                    query: "latest news in my short news feed",
                    note: "Focus on the newest important developments from the fast news feed."
                )
            ),
            ShortNewsQuickAction(
                id: "spicy_discussions",
                title: "Spicy Discussions",
                systemImage: "flame",
                prompt: "What are the spiciest discussions in my short news feed right now?",
                screenContext: AssistantScreenContext(
                    screenType: "short_news_feed",
                    screenTitle: "Fast Read",
                    visibleContentIds: visibleItemIds,
                    query: "spiciest discussions in my short news feed",
                    note: "Pull out the sharpest disagreements, surprising takes, and most interesting discussion threads from the fast news feed."
                )
            ),
        ]
    }
}
