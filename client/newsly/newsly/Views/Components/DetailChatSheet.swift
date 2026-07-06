//
//  DetailChatSheet.swift
//  newsly
//

import SwiftUI

struct DetailChatSheet<PodcastAudioCard: View>: View {
    private let chatError: String?
    private let isStartingChat: Bool
    private let showsPodcastAudioCard: Bool
    private let onClose: () -> Void
    private let onStartChat: () -> Void
    private let onDigDeeper: () -> Void
    private let onCouncilChat: () -> Void
    private let onDeepResearch: () -> Void
    private let podcastAudioCard: () -> PodcastAudioCard

    init(
        chatError: String?,
        isStartingChat: Bool,
        showsPodcastAudioCard: Bool,
        onClose: @escaping () -> Void,
        onStartChat: @escaping () -> Void,
        onDigDeeper: @escaping () -> Void,
        onCouncilChat: @escaping () -> Void,
        onDeepResearch: @escaping () -> Void,
        @ViewBuilder podcastAudioCard: @escaping () -> PodcastAudioCard
    ) {
        self.chatError = chatError
        self.isStartingChat = isStartingChat
        self.showsPodcastAudioCard = showsPodcastAudioCard
        self.onClose = onClose
        self.onStartChat = onStartChat
        self.onDigDeeper = onDigDeeper
        self.onCouncilChat = onCouncilChat
        self.onDeepResearch = onDeepResearch
        self.podcastAudioCard = podcastAudioCard
    }

    var body: some View {
        VStack(spacing: 0) {
            MiniSheetHeader(dismiss: onClose)

            ScrollView {
                VStack(spacing: 12) {
                    if let chatError {
                        errorBanner(chatError)
                    }

                    LazyVGrid(columns: chatTileColumns, spacing: 10) {
                        DetailChatActionTile(
                            icon: "message",
                            title: "Start chat",
                            disabled: isStartingChat,
                            accessibilityIdentifier: "content.chat.start",
                            action: onStartChat
                        )

                        DetailChatActionTile(
                            icon: "doc.text.magnifyingglass",
                            title: "Dig deeper",
                            disabled: isStartingChat,
                            accessibilityIdentifier: "content.chat.dig_deeper",
                            action: onDigDeeper
                        )

                        DetailChatActionTile(
                            icon: "person.3.sequence.fill",
                            title: "Council Chat",
                            disabled: isStartingChat,
                            accessibilityIdentifier: "content.chat.council",
                            action: onCouncilChat
                        )

                        DetailChatActionTile(
                            icon: "magnifyingglass.circle.fill",
                            title: "Deep Research",
                            badge: "2-5 min",
                            disabled: isStartingChat,
                            accessibilityIdentifier: "content.chat.deep_research",
                            action: onDeepResearch
                        )
                    }

                    if showsPodcastAudioCard {
                        podcastAudioCard()
                    }
                }
                .padding(.horizontal, Spacing.appHorizontalMargin)

                Color.clear.frame(height: 16)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .background(Color.surfacePrimary)
        .accessibilityLabel("Chat actions")
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("content.chat.sheet")
    }

    private var chatTileColumns: [GridItem] {
        [
            GridItem(.flexible(), spacing: 10),
            GridItem(.flexible(), spacing: 10)
        ]
    }

    private func errorBanner(_ message: String) -> some View {
        HStack(spacing: 8) {
            Image(systemName: "exclamationmark.circle.fill")
                .foregroundColor(.statusDestructive)
            Text(message)
                .font(.appFootnote)
                .foregroundColor(.statusDestructive)
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.statusDestructive.opacity(0.1))
        .clipShape(RoundedRectangle(cornerRadius: 10))
    }
}

private struct DetailChatActionTile: View {
    let icon: String
    let title: String
    var badge: String?
    var disabled = false
    let accessibilityIdentifier: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 12) {
                HStack(alignment: .top, spacing: 8) {
                    ChatSheetIcon(icon, color: .readerBodyText)

                    Spacer(minLength: 0)

                    if let badge {
                        Text(badge)
                            .font(.appCaption2.weight(.semibold).monospacedDigit())
                            .foregroundColor(Color.onSurfaceSecondary)
                            .padding(.horizontal, 7)
                            .padding(.vertical, 4)
                            .background(Color.surfaceTertiary.opacity(0.82))
                            .clipShape(Capsule())
                    }
                }

                Spacer(minLength: 0)

                Text(title)
                    .font(.appSubheadline.weight(.semibold))
                    .foregroundColor(Color.onSurface)
                    .lineLimit(2)
                    .minimumScaleFactor(0.84)
                    .multilineTextAlignment(.leading)
            }
            .chatTileSurface()
        }
        .buttonStyle(ChatSheetButtonStyle())
        .disabled(disabled)
        .opacity(disabled ? 0.55 : 1)
        .accessibilityLabel(badge.map { "\(title), \($0)" } ?? title)
        .accessibilityIdentifier(accessibilityIdentifier)
    }
}

struct ChatSheetIcon: View {
    private let icon: String
    private let color: Color

    init(_ icon: String, color: Color) {
        self.icon = icon
        self.color = color
    }

    var body: some View {
        Image(systemName: icon)
            .font(.appSymbol(size: 18, weight: .semibold))
            .foregroundColor(color)
            .frame(width: 42, height: 42)
            .background(color.opacity(0.15))
            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
    }
}

private struct ChatSheetButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.96 : 1)
            .opacity(configuration.isPressed ? 0.88 : 1)
            .animation(AppMotion.press, value: configuration.isPressed)
    }
}

extension View {
    func chatTileSurface() -> some View {
        self
            .padding(12)
            .frame(maxWidth: .infinity, minHeight: 112, alignment: .topLeading)
            .background(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .fill(Color.surfaceSecondary)
                    .appShadow(.floating)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .stroke(Color.outlineVariant.opacity(0.34), lineWidth: 0.5)
            )
    }

    func chatWideActionSurface() -> some View {
        self
            .padding(.vertical, 12)
            .padding(.horizontal, 12)
            .frame(minHeight: 66)
            .background(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .fill(Color.surfaceSecondary)
                    .appShadow(.floating)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .stroke(Color.outlineVariant.opacity(0.32), lineWidth: 0.5)
            )
    }
}
