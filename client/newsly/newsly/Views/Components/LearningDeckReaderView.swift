//
//  LearningDeckReaderView.swift
//  newsly
//

import SwiftUI
import UIKit

private enum LearningDeckReaderLayout {
    static let compactChatHeight: CGFloat = 300
    static let regularChatHeight: CGFloat = 340
    static let maxChatHeight: CGFloat = 400
    static let landscapeHorizontalSafeReserve: CGFloat = 76
}

struct LearningDeckReaderView: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @State private var viewModel: LearningDeckReaderViewModel
    @State private var webController = LearningDeckReaderWebController()
    @State private var showLandscapeChat = false

    let deck: LearningDeck
    let viewerURL: URL?
    let onClose: (() -> Void)?

    @MainActor
    init(
        deck: LearningDeck,
        viewerURL: URL?,
        onClose: (() -> Void)? = nil,
        chatService: (any LearningDeckReaderChatServicing)? = nil
    ) {
        self.deck = deck
        self.viewerURL = viewerURL
        self.onClose = onClose
        _viewModel = State(
            initialValue: RootDependencyFactory.makeLearningDeckReaderViewModel(
                deck: deck,
                chatService: chatService
            )
        )
    }

    var body: some View {
        GeometryReader { geometry in
            let isLandscape = geometry.size.width > geometry.size.height

            Group {
                if let url = viewModel.resolvedViewerURL {
                    readerContent(url: url, geometry: geometry, isLandscape: isLandscape)
                } else {
                    generatingContent
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(Color.surfacePrimary.ignoresSafeArea())
        }
        .task {
            viewModel.handleAppear()
            viewModel.prepareViewer(initialURL: viewerURL)
        }
        .onDisappear {
            viewModel.handleDisappear()
            viewModel.cancelViewerResolution()
        }
        .sheet(isPresented: $showLandscapeChat) {
            LearningDeckChatPanel(
                deck: deck,
                viewModel: viewModel,
                isExpanded: .constant(true),
                isPeekable: false
            )
            .presentationDetents([.medium, .large])
            .presentationDragIndicator(.visible)
        }
    }

    // MARK: - Reader content (viewer available)

    private func readerContent(url: URL, geometry: GeometryProxy, isLandscape: Bool) -> some View {
        let landscapeLeadingInset = isLandscape
            ? max(geometry.safeAreaInsets.leading, LearningDeckReaderLayout.landscapeHorizontalSafeReserve)
            : 0
        let landscapeTrailingInset = isLandscape
            ? max(geometry.safeAreaInsets.trailing, LearningDeckReaderLayout.landscapeHorizontalSafeReserve)
            : 0

        return VStack(spacing: 0) {
            deckRegion(url: url, isLandscape: isLandscape)
                .padding(.leading, landscapeLeadingInset)
                .padding(.trailing, landscapeTrailingInset)
                .padding(.bottom, isLandscape ? geometry.safeAreaInsets.bottom : 0)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .ignoresSafeArea(.keyboard, edges: .bottom)

            if !isLandscape {
                Divider()
                    .overlay(Color.outlineVariant.opacity(0.22))

                LearningDeckChatPanel(
                    deck: deck,
                    viewModel: viewModel,
                    isExpanded: .constant(true),
                    isPeekable: false
                )
                .frame(height: chatHeight(for: geometry.size))
                .transition(.move(edge: .bottom).combined(with: .opacity))
            }
        }
        .animation(AppMotion.subtle, value: isLandscape)
    }

    private func deckRegion(url: URL, isLandscape: Bool) -> some View {
        deckWebView(url: url)
            .overlay { loadOverlay }
            .overlay(alignment: .top) { topChrome(isLandscape: isLandscape) }
    }

    private func deckWebView(url: URL) -> some View {
        LearningDeckWebView(
            url: url.newslyBrowserCompatibleLocalURL,
            controller: webController,
            slideContext: $viewModel.currentSlideContext
        )
        .background(Color.surfacePrimary)
        .accessibilityElement(children: .contain)
        .accessibilityValue(slideAccessibilityValue)
        .accessibilityIdentifier("learning_deck.reader.webview")
    }

    @ViewBuilder
    private var loadOverlay: some View {
        switch webController.phase {
        case .loading:
            VStack(spacing: 12) {
                ProgressView()
                    .tint(Color.brandPrimary)
                Text("Getting your deck ready…")
                    .font(.terracottaBodySmall)
                    .foregroundStyle(Color.onSurfaceSecondary)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(Color.surfacePrimary)
            .accessibilityIdentifier("learning_deck.reader.loading")
        case .failed:
            webLoadErrorCard
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(Color.surfacePrimary)
        case .loaded:
            EmptyView()
        }
    }

    private var webLoadErrorCard: some View {
        VStack(spacing: 12) {
            Image(systemName: "exclamationmark.triangle")
                .font(.appSymbol(size: 26, weight: .regular))
                .foregroundStyle(Color.onSurfaceSecondary)
            Text(deck.displayTitle)
                .font(.terracottaHeadlineSmall)
                .foregroundStyle(Color.onSurface)
                .multilineTextAlignment(.center)
                .lineLimit(2)
            Text("This deck didn't load. Want to try again?")
                .font(.terracottaBodySmall)
                .foregroundStyle(Color.onSurfaceSecondary)
                .multilineTextAlignment(.center)
            pillButton("Try again") { webController.reload() }
                .padding(.top, 4)
        }
        .padding(.horizontal, 32)
        .accessibilityIdentifier("learning_deck.reader.load_error")
    }

    // MARK: - Generating content (viewer not yet available)

    private var generatingContent: some View {
        VStack(spacing: 14) {
            if viewModel.viewerResolutionFailed {
                Image(systemName: "exclamationmark.triangle")
                    .font(.appSymbol(size: 26, weight: .regular))
                    .foregroundStyle(Color.onSurfaceSecondary)
                Text(deck.displayTitle)
                    .font(.terracottaHeadlineSmall)
                    .foregroundStyle(Color.onSurface)
                    .lineLimit(2)
                Text(viewModel.generationNote ?? "That deck didn't come together.")
                    .font(.terracottaBodySmall)
                    .foregroundStyle(Color.onSurfaceSecondary)
                pillButton("Try again") { viewModel.retryViewerResolution() }
                    .padding(.top, 4)
            } else {
                ProgressView()
                    .controlSize(.large)
                    .tint(Color.brandPrimary)
                Text(deck.displayTitle)
                    .font(.terracottaHeadlineSmall)
                    .foregroundStyle(Color.onSurface)
                    .lineLimit(2)
                Text(viewModel.generationStatusLabel)
                    .font(.terracottaBodyMedium.weight(.semibold))
                    .foregroundStyle(Color.onSurface)
                if let note = viewModel.generationNote {
                    Text(note)
                        .font(.terracottaBodySmall)
                        .foregroundStyle(Color.onSurfaceSecondary)
                }
            }
        }
        .multilineTextAlignment(.center)
        .padding(.horizontal, 32)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .overlay(alignment: .topLeading) {
            closeButton
                .padding(.leading, Spacing.appHorizontalMargin)
                .padding(.top, closeButtonTopPadding)
        }
        .accessibilityIdentifier("learning_deck.reader.generating")
    }

    // MARK: - Top chrome (close, progress, slide nav)

    private func topChrome(isLandscape: Bool) -> some View {
        ZStack {
            if let progress = slideProgress {
                progressPill(progress)
            }

            HStack(spacing: 8) {
                closeButton
                Spacer(minLength: 8)
                if webController.phase == .loaded {
                    navCluster(isLandscape: isLandscape)
                }
            }
        }
        .padding(.horizontal, Spacing.appHorizontalMargin)
        .padding(.top, closeButtonTopPadding)
    }

    private var closeButton: some View {
        Button {
            if let onClose {
                onClose()
            } else {
                dismiss()
            }
        } label: {
            ZStack {
                Circle()
                    .fill(Color.surfacePrimary.opacity(0.001))
                Image(systemName: "xmark")
                    .font(.appSymbol(size: 16, weight: .semibold))
                    .foregroundStyle(Color.onSurface)
            }
            .frame(width: 44, height: 44)
            .learningDeckReaderCircleSurface(tint: Color.surfacePrimary, isEnabled: true)
            .appShadow(.subtle)
            .contentShape(Circle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Close")
        .accessibilityIdentifier("learning_deck.reader.close")
    }

    private func navCluster(isLandscape: Bool) -> some View {
        HStack(spacing: 2) {
            navButton("chevron.left", label: "Previous slide") { webController.goPrevious() }
            navButton("chevron.right", label: "Next slide") { webController.goNext() }
            navButton("square.grid.2x2", label: "Slide overview") { webController.toggleOverview() }
            if isLandscape {
                navButton("bubble.left.and.text.bubble.right", label: "Open chat") {
                    showLandscapeChat = true
                }
            }
        }
        .padding(.horizontal, 4)
        .frame(height: 40)
        .learningDeckReaderCapsuleSurface(tint: Color.surfacePrimary, isEnabled: true)
        .appShadow(.subtle)
    }

    private func navButton(_ systemName: String, label: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: systemName)
                .font(.appSymbol(size: 14, weight: .semibold))
                .foregroundStyle(Color.onSurface)
                .frame(width: 36, height: 36)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(label)
    }

    private func progressPill(_ progress: (current: Int, total: Int, fraction: Double)) -> some View {
        HStack(spacing: 8) {
            Text("\(progress.current) / \(progress.total)")
                .font(.terracottaBodySmall.weight(.semibold))
                .monospacedDigit()
                .foregroundStyle(Color.onSurface)
            ZStack(alignment: .leading) {
                Capsule()
                    .fill(Color.outlineVariant.opacity(0.4))
                    .frame(width: 44, height: 3)
                Capsule()
                    .fill(Color.brandPrimary)
                    .frame(width: max(2, CGFloat(progress.fraction) * 44), height: 3)
            }
        }
        .padding(.horizontal, 12)
        .frame(height: 32)
        .learningDeckReaderCapsuleSurface(tint: Color.surfacePrimary, isEnabled: false)
        .appShadow(.subtle)
        .animation(AppMotion.subtle, value: progress.current)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Slide \(progress.current) of \(progress.total)")
    }

    private func pillButton(_ title: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title)
                .font(.terracottaBodyMedium.weight(.semibold))
                .foregroundStyle(Color.onSurface)
                .padding(.horizontal, 18)
                .frame(height: 44)
                .learningDeckReaderCapsuleSurface(tint: Color.surfacePrimary, isEnabled: true)
                .contentShape(Capsule())
        }
        .buttonStyle(PressableButtonStyle())
    }

    // MARK: - Derived state

    private var slideProgress: (current: Int, total: Int, fraction: Double)? {
        let context = viewModel.currentSlideContext
        guard let total = context.totalSlides, total > 0 else { return nil }
        let current = min((context.horizontalIndex ?? 0) + 1, total)
        return (current, total, Double(current) / Double(total))
    }

    private var slideAccessibilityValue: String {
        let context = viewModel.currentSlideContext
        guard context.horizontalIndex != nil || context.verticalIndex != nil else { return "" }
        let horizontal = (context.horizontalIndex ?? 0) + 1
        var label = "Slide \(horizontal)"
        if let vertical = context.verticalIndex, vertical > 0 {
            label += ".\(vertical + 1)"
        }
        if let total = context.totalSlides, total > 0 {
            label += " of \(total)"
        }
        if let title = context.title, !title.isEmpty {
            label += ", \(title)"
        }
        return label
    }

    private func chatHeight(for size: CGSize) -> CGFloat {
        let preferred = size.height < 760
            ? LearningDeckReaderLayout.compactChatHeight
            : LearningDeckReaderLayout.regularChatHeight
        let cap = dynamicTypeSize.isAccessibilitySize
            ? size.height * 0.62
            : min(LearningDeckReaderLayout.maxChatHeight, size.height * 0.46)
        return min(max(preferred, size.height * 0.34), cap)
    }

    private var closeButtonTopPadding: CGFloat {
        // Overlays inside a cover-level .ignoresSafeArea() report a zero geometry
        // inset, so read the real top inset from the key window instead.
        let inset = UIApplication.shared.connectedScenes
            .compactMap { ($0 as? UIWindowScene)?.keyWindow }
            .first?.safeAreaInsets.top ?? 0
        return max(inset, 50) + 6
    }
}
