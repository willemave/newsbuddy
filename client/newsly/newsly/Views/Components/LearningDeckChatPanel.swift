//
//  LearningDeckChatPanel.swift
//  newsly
//

import SwiftUI
import UIKit

struct LearningDeckChatPanel: View {
    let deck: LearningDeck
    @Bindable var viewModel: LearningDeckReaderViewModel
    @Binding var isExpanded: Bool
    let isPeekable: Bool

    @StateObject private var feedOptionActionModel = AssistantFeedOptionActionModel()

    var body: some View {
        Group {
            if isPeekable && !isExpanded {
                peekBar
            } else {
                expandedPanel
            }
        }
        .background(.ultraThinMaterial)
        .overlay(alignment: .top) {
            Rectangle()
                .fill(Color.outlineVariant.opacity(0.24))
                .frame(height: 0.5)
        }
        .scrollDismissesKeyboard(.interactively)
    }

    private var peekBar: some View {
        Button {
            isExpanded = true
        } label: {
            VStack(spacing: 6) {
                Capsule()
                    .fill(Color.outlineVariant.opacity(0.45))
                    .frame(width: 36, height: 5)
                    .padding(.top, 8)

                HStack(spacing: 8) {
                    Image(systemName: "bubble.left.and.text.bubble.right")
                        .font(.appSymbol(size: 14, weight: .semibold))
                        .foregroundStyle(Color.brandPrimary)

                    Text("Ask about this slide")
                        .font(.terracottaBodyMedium.weight(.semibold))
                        .foregroundStyle(Color.onSurface)

                    Spacer()

                    if let label = slideLabel {
                        slidePill(label)
                    }
                }
                .padding(.horizontal, Spacing.appHorizontalMargin)
            }
            .frame(maxWidth: .infinity)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Open deck chat")
        .accessibilityIdentifier("learning_deck.chat.peek")
    }

    private var expandedPanel: some View {
        VStack(spacing: 0) {
            header

            transcript

            LearningDeckChatComposer(viewModel: viewModel)
                .padding(.horizontal, Spacing.appHorizontalMargin)
                .padding(.top, 8)
                .padding(.bottom, 10)
        }
    }

    private var header: some View {
        HStack(spacing: 8) {
            Image(systemName: "bubble.left.and.text.bubble.right")
                .font(.appSymbol(size: 14, weight: .semibold))
                .foregroundStyle(Color.brandPrimary)

            Text("Deck chat")
                .font(.terracottaBodyMedium.weight(.semibold))
                .foregroundStyle(Color.onSurface)

            Spacer()

            if let label = slideLabel {
                slidePill(label)
            }

            if isPeekable {
                Button {
                    isExpanded = false
                } label: {
                    Image(systemName: "chevron.down")
                        .font(.appSymbol(size: 13, weight: .semibold))
                        .foregroundStyle(Color.onSurfaceSecondary)
                        .frame(width: 32, height: 32)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Collapse chat")
                .accessibilityIdentifier("learning_deck.chat.collapse")
            }
        }
        .padding(.horizontal, Spacing.appHorizontalMargin)
        .padding(.top, 10)
        .padding(.bottom, 8)
        .accessibilityIdentifier("learning_deck.chat.header")
    }

    private func slidePill(_ label: String) -> some View {
        Text(label)
            .font(.terracottaBodySmall)
            .foregroundStyle(Color.onSurfaceSecondary)
            .lineLimit(1)
            .padding(.horizontal, 9)
            .padding(.vertical, 5)
            .learningDeckReaderCapsuleSurface(tint: Color.surfaceSecondary, isEnabled: false)
    }

    private var transcript: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 10) {
                    if viewModel.timeline.isEmpty && !viewModel.isSending {
                        emptyState
                    } else {
                        ForEach(viewModel.timeline) { item in
                            MessageRow(
                                item: item,
                                retryingCouncilChildSessionId: nil,
                                feedOptionActionModel: feedOptionActionModel,
                                onRetrySend: { viewModel.performSendMessage(text: $0) },
                                onRetryCouncilCandidate: { _ in },
                                onDigDeeper: { viewModel.performSendMessage(text: "Dig deeper into this: \"\($0)\"") },
                                onShare: { UIPasteboard.general.string = $0 }
                            )
                            .id(item.id)
                        }

                        if viewModel.isSending {
                            ThinkingBubbleView(
                                startDate: viewModel.thinkingStartedAt,
                                statusText: "Reading the current slide"
                            )
                            .id("learning-deck-thinking")
                            .transition(.opacity.combined(with: .move(edge: .bottom)))
                        }
                    }

                    if let errorMessage = viewModel.errorMessage, !errorMessage.isEmpty {
                        LearningDeckChatErrorRow(message: errorMessage)
                    }
                }
                .padding(.horizontal, Spacing.appHorizontalMargin)
                .padding(.top, 8)
                .padding(.bottom, 18)
                .animation(.easeOut(duration: 0.2), value: viewModel.timeline.count)
                .animation(.easeOut(duration: 0.2), value: viewModel.isSending)
            }
            .onChange(of: viewModel.timeline.last?.id) { _, newId in
                guard let newId else { return }
                scrollToBottom(newId, proxy: proxy)
            }
            .onChange(of: viewModel.isSending) { _, isSending in
                if isSending {
                    scrollToBottom("learning-deck-thinking", proxy: proxy)
                } else if let lastId = viewModel.timeline.last?.id {
                    scrollToBottom(lastId, proxy: proxy)
                }
            }
        }
    }

    private var emptyState: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(deck.displayTitle)
                .font(.terracottaBodyMedium.weight(.semibold))
                .foregroundStyle(Color.onSurface)
                .lineLimit(2)

            Text("Curious about this slide? Tap one, or ask me anything.")
                .font(.terracottaBodySmall)
                .foregroundStyle(Color.onSurfaceSecondary)

            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(starterPrompts, id: \.self) { prompt in
                        starterChip(prompt)
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(
            Color.surfacePrimary.opacity(0.5),
            in: RoundedRectangle(cornerRadius: CornerRadius.control, style: .continuous)
        )
        .accessibilityIdentifier("learning_deck.chat.empty")
    }

    private func starterChip(_ prompt: String) -> some View {
        Button {
            viewModel.performSendMessage(text: prompt)
        } label: {
            Text(prompt)
                .font(.terracottaBodySmall.weight(.semibold))
                .foregroundStyle(Color.onSurface)
                .lineLimit(1)
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
                .learningDeckReaderCapsuleSurface(tint: Color.surfaceSecondary, isEnabled: true)
                .contentShape(Capsule())
        }
        .buttonStyle(PressableButtonStyle())
        .accessibilityIdentifier("learning_deck.chat.starter")
    }

    private var starterPrompts: [String] {
        var prompts = ["Explain this simply", "Why does this matter?", "Give me an example"]
        if let title = nonEmptyTrimmed(viewModel.currentSlideContext.title) {
            prompts.insert("Tell me more about \(title)", at: 0)
        }
        return prompts
    }

    private var slideLabel: String? {
        let context = viewModel.currentSlideContext
        guard context.horizontalIndex != nil || context.verticalIndex != nil else {
            return nil
        }
        let horizontal = (context.horizontalIndex ?? 0) + 1
        if let total = context.totalSlides, total > 0 {
            return "Slide \(min(horizontal, total)) / \(total)"
        }
        if let vertical = context.verticalIndex, vertical > 0 {
            return "Slide \(horizontal).\(vertical + 1)"
        }
        return "Slide \(horizontal)"
    }

    private func scrollToBottom<ID: Hashable>(_ target: ID, proxy: ScrollViewProxy) {
        withAnimation(.easeOut(duration: 0.2)) {
            proxy.scrollTo(target, anchor: .bottom)
        }
        DispatchQueue.main.async {
            withAnimation(.easeOut(duration: 0.2)) {
                proxy.scrollTo(target, anchor: .bottom)
            }
        }
    }
}

private struct LearningDeckChatErrorRow: View {
    let message: String

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "exclamationmark.triangle")
                .font(.appCaption)
            Text(message)
                .font(.terracottaBodySmall)
                .fixedSize(horizontal: false, vertical: true)
        }
        .foregroundStyle(Color.statusDestructive)
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .background(Color.statusDestructive.opacity(0.08), in: RoundedRectangle(cornerRadius: 10))
        .accessibilityIdentifier("learning_deck.chat.error")
    }
}
