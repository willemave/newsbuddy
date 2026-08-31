//
//  LearningDeckChatPanel.swift
//  newsly
//

import SwiftUI
import UIKit

enum LearningDeckChatPresentation: Equatable {
    case peek
    case compact
    case focus
}

enum LearningDeckChatHeightPolicy {
    private static let compactHeight: CGFloat = 230
    private static let regularHeight: CGFloat = 280
    private static let accessibilityHeight: CGFloat = 340

    static func height(
        for presentation: LearningDeckChatPresentation,
        size: CGSize,
        isAccessibilitySize: Bool
    ) -> CGFloat? {
        switch presentation {
        case .peek:
            return nil
        case .compact:
            let preferred = if isAccessibilitySize {
                accessibilityHeight
            } else if size.height < 760 {
                compactHeight
            } else {
                regularHeight
            }
            let maximumFraction = isAccessibilitySize ? 0.58 : 0.42
            return min(preferred, size.height * maximumFraction)
        case .focus:
            return size.height * 0.74
        }
    }
}

enum LearningDeckChatFlyoverInteraction {
    static func target(
        from presentation: LearningDeckChatPresentation,
        for translation: CGSize
    ) -> LearningDeckChatPresentation {
        guard abs(translation.height) >= 24 else { return presentation }
        guard abs(translation.height) > abs(translation.width) else { return presentation }
        if translation.height < 0 {
            switch presentation {
            case .peek: return .compact
            case .compact, .focus: return .focus
            }
        }
        switch presentation {
        case .peek, .compact: return .peek
        case .focus: return .compact
        }
    }
}

struct LearningDeckChatFlyover: View {
    @Bindable var viewModel: LearningDeckReaderViewModel
    let feedOptionActionModel: AssistantFeedOptionActionModel
    @Binding var presentation: LearningDeckChatPresentation

    var body: some View {
        Group {
            if presentation != .peek {
                LearningDeckChatPanel(
                    viewModel: viewModel,
                    feedOptionActionModel: feedOptionActionModel,
                    isFocused: presentation == .focus,
                    onResize: {
                        presentation = presentation == .focus ? .compact : .focus
                    },
                    onCollapse: {
                        presentation = .peek
                    },
                    onHeaderDrag: { translation in
                        presentation = LearningDeckChatFlyoverInteraction.target(
                            from: presentation,
                            for: translation
                        )
                    }
                )
            } else {
                peekBar
            }
        }
        .background(Color.surfacePrimary.opacity(0.98))
        .overlay(alignment: .top) {
            Rectangle()
                .fill(Color.outlineVariant.opacity(0.24))
                .frame(height: 0.5)
        }
    }

    private var peekBar: some View {
        Button {
            presentation = .compact
        } label: {
            VStack(spacing: 6) {
                Capsule()
                    .fill(Color.outlineVariant.opacity(0.45))
                    .frame(width: 36, height: 5)
                    .padding(.top, 10)

                HStack(spacing: 8) {
                    Image(systemName: "bubble.left.and.text.bubble.right")
                        .font(.appSymbol(size: 14, weight: .semibold))
                        .foregroundStyle(Color.brandPrimary)

                    Text("Ask about this slide")
                        .font(.terracottaBodyMedium.weight(.semibold))
                        .foregroundStyle(Color.onSurface)

                    Spacer()

                    if viewModel.isSending {
                        ProgressView()
                            .controlSize(.small)
                            .tint(Color.brandPrimary)
                            .accessibilityLabel("Deck chat is responding")
                    }

                    if let label = learningDeckSlideLabel(for: viewModel.currentSlideContext) {
                        LearningDeckChatSlidePill(label: label)
                    }
                }
                .padding(.horizontal, Spacing.appHorizontalMargin)
                .padding(.bottom, 14)
            }
            .frame(maxWidth: .infinity, minHeight: 72)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .simultaneousGesture(
            DragGesture(minimumDistance: 12)
                .onEnded { value in
                    presentation = LearningDeckChatFlyoverInteraction.target(
                        from: presentation,
                        for: value.translation
                    )
                }
        )
        .accessibilityLabel("Open deck chat")
        .accessibilityIdentifier("learning_deck.chat.peek")
    }
}

struct LearningDeckChatPanel: View {
    @Bindable var viewModel: LearningDeckReaderViewModel
    let isFocused: Bool
    let onResize: (() -> Void)?
    let onCollapse: (() -> Void)?
    let onHeaderDrag: ((CGSize) -> Void)?

    let feedOptionActionModel: AssistantFeedOptionActionModel
    @State private var isNearBottom = true

    init(
        viewModel: LearningDeckReaderViewModel,
        feedOptionActionModel: AssistantFeedOptionActionModel,
        isFocused: Bool = false,
        onResize: (() -> Void)? = nil,
        onCollapse: (() -> Void)? = nil,
        onHeaderDrag: ((CGSize) -> Void)? = nil
    ) {
        self.viewModel = viewModel
        self.feedOptionActionModel = feedOptionActionModel
        self.isFocused = isFocused
        self.onResize = onResize
        self.onCollapse = onCollapse
        self.onHeaderDrag = onHeaderDrag
    }

    var body: some View {
        expandedPanel
            .background(Color.surfacePrimary.opacity(0.98))
            .overlay(alignment: .top) {
                Rectangle()
                    .fill(Color.outlineVariant.opacity(0.24))
                    .frame(height: 0.5)
            }
            .scrollDismissesKeyboard(.interactively)
    }

    private var expandedPanel: some View {
        VStack(spacing: 0) {
            header

            transcript

            LearningDeckChatComposer(viewModel: viewModel)
                .padding(.horizontal, Spacing.appHorizontalMargin)
                .padding(.top, 8)
                .padding(.bottom, 18)
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

            if let label = learningDeckSlideLabel(for: viewModel.currentSlideContext) {
                LearningDeckChatSlidePill(label: label)
            }

            if let onResize {
                Button(action: onResize) {
                    Image(
                        systemName: isFocused
                            ? "arrow.down.right.and.arrow.up.left"
                            : "arrow.up.left.and.arrow.down.right"
                    )
                    .font(.appSymbol(size: 13, weight: .semibold))
                    .foregroundStyle(Color.onSurfaceSecondary)
                    .frame(width: 32, height: 32)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel(isFocused ? "Use compact chat" : "Use focused chat")
                .accessibilityIdentifier("learning_deck.chat.resize")
            }

            if let onCollapse {
                Button(action: onCollapse) {
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
        .contentShape(Rectangle())
        .simultaneousGesture(
            DragGesture(minimumDistance: 12)
                .onEnded { value in
                    onHeaderDrag?(value.translation)
                }
        )
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

                        if viewModel.isSending && !viewModel.hasVisiblePartialResponse {
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
                .animation(AppMotion.subtle, value: viewModel.timeline.count)
                .animation(AppMotion.subtle, value: viewModel.isSending)
            }
            .onScrollGeometryChange(for: Bool.self) { geometry in
                let distanceFromBottom =
                    geometry.contentSize.height
                    - geometry.visibleRect.maxY
                    + geometry.contentInsets.bottom
                return distanceFromBottom < 48
            } action: { _, newValue in
                isNearBottom = newValue
            }
            .onChange(of: viewModel.timeline.last?.id) { _, newId in
                guard let newId else { return }
                guard isNearBottom || viewModel.timeline.last?.message.isUser == true else {
                    return
                }
                scrollToBottom(newId, proxy: proxy)
            }
            .onChange(of: viewModel.timeline.last?.message.content) { _, _ in
                guard isNearBottom, let lastId = viewModel.timeline.last?.id else { return }
                DispatchQueue.main.async {
                    proxy.scrollTo(lastId, anchor: .bottom)
                }
            }
            .onChange(of: viewModel.isSending) { _, isSending in
                if isSending {
                    guard isNearBottom else { return }
                    scrollToBottom("learning-deck-thinking", proxy: proxy)
                } else if isNearBottom, let lastId = viewModel.timeline.last?.id {
                    scrollToBottom(lastId, proxy: proxy)
                }
            }
        }
    }

    private var emptyState: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Ask for an explanation, example, or implication.")
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

    private func scrollToBottom<ID: Hashable>(_ target: ID, proxy: ScrollViewProxy) {
        withAnimation(AppMotion.subtle) {
            proxy.scrollTo(target, anchor: .bottom)
        }
        DispatchQueue.main.async {
            withAnimation(AppMotion.subtle) {
                proxy.scrollTo(target, anchor: .bottom)
            }
        }
    }
}

private struct LearningDeckChatSlidePill: View {
    let label: String

    var body: some View {
        Text(label)
            .font(.terracottaBodySmall)
            .foregroundStyle(Color.onSurfaceSecondary)
            .lineLimit(1)
            .padding(.horizontal, 9)
            .padding(.vertical, 5)
            .learningDeckReaderCapsuleSurface(tint: Color.surfaceSecondary, isEnabled: false)
    }
}

private func learningDeckSlideLabel(for context: LearningDeckSlideContext) -> String? {
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
