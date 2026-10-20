//
//  ArticleReaderView.swift
//  newsly
//

import SwiftUI
import UIKit

/// One reading surface for an item: stored text in the reader, or the original
/// page in place. The top trailing button flips between them; Deck works in both.
struct ArticleReaderView: View {
    enum Mode {
        case text
        case web
    }

    let content: ContentDetail
    let articleBody: ContentBody?
    let isLoading: Bool
    let errorMessage: String?
    let showsReaderText: Bool
    let onRetry: () -> Void
    let onDigDeeper: (String) -> Void

    @Environment(\.dismiss) private var dismiss
    @Environment(RootDependencyFactory.self) private var dependencyFactory
    @State private var mode: Mode
    @State private var hasOpenedWeb: Bool
    @State private var webState: ArticleWebView.LoadState = .loading
    @State private var webReloadID = 0
    @State private var showsDeckCreate = false
    @State private var deckNotice: ViewAlert?
    @AppStorage("articleReaderBodyFontSize", store: SharedContainer.userDefaults)
    private var storedBodyFontSize: Double = 18

    private let minBodyFontSize: CGFloat = 16
    private let maxBodyFontSize: CGFloat = 24
    private var bodyFontSize: CGFloat { CGFloat(storedBodyFontSize).clamped(to: minBodyFontSize...maxBodyFontSize) }

    init(
        content: ContentDetail,
        articleBody: ContentBody?,
        isLoading: Bool,
        errorMessage: String?,
        showsReaderText: Bool,
        onRetry: @escaping () -> Void,
        onDigDeeper: @escaping (String) -> Void
    ) {
        self.content = content
        self.articleBody = articleBody
        self.isLoading = isLoading
        self.errorMessage = errorMessage
        self.showsReaderText = showsReaderText
        self.onRetry = onRetry
        self.onDigDeeper = onDigDeeper
        _mode = State(initialValue: showsReaderText ? .text : .web)
        _hasOpenedWeb = State(initialValue: !showsReaderText)
    }

    var body: some View {
        VStack(spacing: 0) {
            readerToolbar

            // Both surfaces stay alive once opened so flipping keeps scroll and page state.
            ZStack {
                if showsReaderText {
                    readerText
                        .opacity(mode == .text ? 1 : 0)
                        .allowsHitTesting(mode == .text)
                        .accessibilityHidden(mode != .text)
                }
                if hasOpenedWeb, let originalURL {
                    ArticleWebView(url: originalURL, state: $webState, reloadID: webReloadID)
                        .overlay {
                            if webState == .loading {
                                ProgressView()
                                    .controlSize(.large)
                                    .tint(Color.onSurfaceSecondary)
                            } else if case .failed(let message) = webState {
                                ContentUnavailableView {
                                    Label("Couldn't load page", systemImage: "wifi.exclamationmark")
                                } description: {
                                    Text(message)
                                } actions: {
                                    Button("Try Again") { webReloadID += 1 }
                                        .accessibilityIdentifier("article.reader.web.retry")
                                }
                                .background(Color.surfacePrimary)
                            }
                        }
                        .ignoresSafeArea(.container, edges: .bottom)
                        .opacity(mode == .web ? 1 : 0)
                        .allowsHitTesting(mode == .web)
                        .accessibilityHidden(mode != .web)
                }
            }
        }
        .background(Color.surfacePrimary.ignoresSafeArea())
        .sheet(isPresented: $showsDeckCreate) {
            LearningDeckContentCreateSheet(
                content: content,
                focusRecorder: dependencyFactory.makeLearningDeckFocusRecorder(),
                learningDeckService: dependencyFactory.learningDeckService,
                onCreated: { _ in
                    dependencyFactory.toastService.show("Deck started", type: .info, duration: 2)
                },
                onNotice: { title, message in
                    deckNotice = ViewAlert(title: title, message: message)
                }
            )
        }
        // The app's toast layer sits under this full-screen cover.
        .withToast(service: dependencyFactory.toastService)
        .alert(item: $deckNotice) { notice in
            Alert(
                title: Text(notice.title),
                message: Text(notice.message),
                dismissButton: .cancel(Text("OK"))
            )
        }
    }

    private var readerText: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                readerHeader

                Group {
                    if let text = articleBody?.text.trimmingCharacters(in: .whitespacesAndNewlines),
                       !text.isEmpty {
                        SelectableMarkdownView(
                            markdown: text,
                            textColor: .appReaderBodyText,
                            baseFont: readerUIFont,
                            adjustsFontForContentSizeCategory: true,
                            onDigDeeper: onDigDeeper
                        )
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .accessibilityIdentifier("article.reader.body")
                    } else if isLoading || errorMessage == nil {
                        readerLoadingState
                    } else if let errorMessage {
                        readerErrorState(errorMessage)
                    }
                }
            }
            .frame(maxWidth: 720, alignment: .leading)
            .padding(.horizontal, Spacing.appHorizontalMargin)
            .padding(.top, 30)
            .padding(.bottom, 56)
            .frame(maxWidth: .infinity)
        }
    }

    private var originalURL: URL? {
        guard let url = URL(string: content.url),
              let scheme = url.scheme?.lowercased(),
              scheme == "http" || scheme == "https"
        else { return nil }
        return url.newslyBrowserCompatibleLocalURL(serverHost: dependencyFactory.appSettings.serverHost)
    }

    private var readerToolbar: some View {
        HStack(spacing: 8) {
            Button {
                dismiss()
            } label: {
                toolbarIcon("xmark", size: 15, weight: .semibold)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Close reader")
            .accessibilityIdentifier("article.reader.close")

            Spacer()

            if mode == .text {
                textSizeControl
            }

            Button {
                showsDeckCreate = true
            } label: {
                toolbarIcon("rectangle.on.rectangle")
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Create Learning Deck")
            .accessibilityIdentifier("article.reader.create_deck")

            if showsReaderText, originalURL != nil {
                Button {
                    hasOpenedWeb = true
                    mode = mode == .text ? .web : .text
                } label: {
                    toolbarIcon(mode == .text ? "safari" : "doc.richtext")
                }
                .buttonStyle(.plain)
                .accessibilityLabel(mode == .text ? "Show original page" : "Show reader")
                .accessibilityIdentifier("article.reader.toggle_mode")
            }
        }
        // Matches the text-size capsule so the bar keeps its height in web mode.
        .frame(minHeight: 50)
        .foregroundStyle(Color.onSurface)
        .padding(.horizontal, Spacing.appHorizontalMargin)
        .padding(.top, 12)
        .padding(.bottom, 10)
        .background(.ultraThinMaterial)
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(Color.outlineVariant.opacity(0.28))
                .frame(height: 0.5)
        }
    }

    private func toolbarIcon(_ name: String, size: CGFloat = 17, weight: Font.Weight = .regular) -> some View {
        Image(systemName: name)
            .font(.appSymbol(size: size, weight: weight))
            .frame(width: 44, height: 44)
            .background(Color.surfaceTertiary.opacity(0.78), in: Circle())
            .contentShape(Rectangle())
    }

    private var textSizeControl: some View {
        HStack(spacing: 2) {
            Button {
                updateBodyFontSize(by: -1)
            } label: {
                Image(systemName: "minus")
                    .font(.appSymbol(size: 14, weight: .semibold))
                    .frame(width: 44, height: 44)
                    .contentShape(Rectangle())
            }
            .disabled(bodyFontSize <= minBodyFontSize)
            .accessibilityLabel("Decrease text size")
            .accessibilityIdentifier("article.reader.text_size.decrease")

            Text("Aa")
                .font(.readerControlLabel)
                .foregroundStyle(Color.onSurface)
                .frame(width: 34, height: 34)
                .accessibilityHidden(true)

            Button {
                updateBodyFontSize(by: 1)
            } label: {
                Image(systemName: "plus")
                    .font(.appSymbol(size: 14, weight: .semibold))
                    .frame(width: 44, height: 44)
                    .contentShape(Rectangle())
            }
            .disabled(bodyFontSize >= maxBodyFontSize)
            .accessibilityLabel("Increase text size")
            .accessibilityIdentifier("article.reader.text_size.increase")
        }
        .buttonStyle(.plain)
        .foregroundStyle(Color.onSurface)
        .padding(3)
        .background(.regularMaterial, in: Capsule())
        .overlay {
            Capsule()
                .stroke(Color.outlineVariant.opacity(0.25), lineWidth: 0.5)
        }
    }

    private var readerHeader: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(content.displayTitle)
                .font(.readerTitle)
                .foregroundStyle(Color.onSurface)
                .lineSpacing(3)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityIdentifier("article.reader.title")

            HStack(spacing: 8) {
                if let source = sourceLabel {
                    Text(source)
                        .font(.terracottaBodySmall.weight(.semibold))
                        .foregroundStyle(Color.onSurfaceSecondary)

                    Circle()
                        .fill(Color.onSurfaceSecondary.opacity(0.45))
                        .frame(width: 3, height: 3)
                }

                ContentTimestampText(
                    rawValue: content.primaryTimestamp,
                    fallback: "Recent"
                )
                .font(.terracottaBodySmall)
                .foregroundStyle(Color.onSurfaceSecondary)
                .accessibilityIdentifier("article.reader.screen")

                if let estimatedReadTime {
                    Circle()
                        .fill(Color.onSurfaceSecondary.opacity(0.45))
                        .frame(width: 3, height: 3)

                    Text(estimatedReadTime)
                        .font(.terracottaBodySmall)
                        .foregroundStyle(Color.onSurfaceSecondary)
                }
            }

            Rectangle()
                .fill(Color.outlineVariant.opacity(0.34))
                .frame(width: 54, height: 1)
                .padding(.top, 2)
        }
    }

    private var readerLoadingState: some View {
        VStack(alignment: .leading, spacing: 14) {
            ForEach(0..<8, id: \.self) { index in
                loadingLine(index)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, 14)
        .accessibilityLabel("Loading article")
    }

    private func readerErrorState(_ message: String) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                Image(systemName: "exclamationmark.circle")
                    .foregroundStyle(Color.statusDestructive)
                Text(message)
                    .font(.terracottaBodyMedium)
                    .foregroundStyle(Color.onSurfaceSecondary)
            }

            Button {
                onRetry()
            } label: {
                HStack(spacing: 8) {
                    Image(systemName: "arrow.clockwise")
                    Text("Try Again")
                }
                .font(.terracottaBodySmall.weight(.semibold))
                .foregroundStyle(Color.surfacePrimary)
                .padding(.horizontal, 14)
                .padding(.vertical, 9)
                .background(Color.brandPrimary, in: Capsule())
            }
            .buttonStyle(.plain)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, 24)
    }

    private var readerUIFont: UIFont {
        UIFont.appSans(size: bodyFontSize, weight: .regular)
    }

    private var estimatedReadTime: String? {
        guard let text = articleBody?.text.trimmingCharacters(in: .whitespacesAndNewlines),
              !text.isEmpty else {
            return nil
        }
        let wordCount = text.split { $0.isWhitespace || $0.isNewline }.count
        guard wordCount > 0 else { return nil }
        let minutes = max(1, Int(ceil(Double(wordCount) / 225.0)))
        return "\(minutes) min read"
    }

    private var sourceLabel: String? {
        guard let source = content.source?.trimmingCharacters(in: .whitespacesAndNewlines),
              !source.isEmpty else {
            return nil
        }
        return source
    }

    private func updateBodyFontSize(by delta: CGFloat) {
        storedBodyFontSize = Double((bodyFontSize + delta).clamped(to: minBodyFontSize...maxBodyFontSize))
    }

    private func loadingLineWidth(for index: Int) -> CGFloat? {
        switch index {
        case 0: return 260
        case 1: return 690
        case 2: return 640
        case 3: return 705
        case 4: return 520
        case 5: return 660
        case 6: return 610
        default: return 380
        }
    }

    private func loadingLine(_ index: Int) -> some View {
        let opacity = index % 3 == 0 ? 0.12 : 0.08
        let height: CGFloat = index == 0 ? 17 : 13

        return RoundedRectangle(cornerRadius: 3)
            .fill(Color.onSurface.opacity(opacity))
            .frame(height: height)
            .frame(maxWidth: loadingLineWidth(for: index), alignment: .leading)
            .redacted(reason: .placeholder)
    }
}

private extension Comparable {
    func clamped(to range: ClosedRange<Self>) -> Self {
        min(max(self, range.lowerBound), range.upperBound)
    }
}
