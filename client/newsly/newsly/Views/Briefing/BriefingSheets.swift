import SwiftUI

struct BriefingDigSheet: View {
    let viewModel: BriefingDigViewModel

    @State private var safariItem: BriefingSafariItem?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    fragmentHeader
                    stateContent
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .padding(.horizontal, Spacing.appHorizontalMargin)
                .padding(.top, 12)
                .padding(.bottom, 32)
            }
            .background(Color.surfacePrimary)
            .navigationTitle("Dig Deeper")
            .navigationBarTitleDisplayMode(.inline)
            .animation(.easeInOut(duration: 0.25), value: viewModel.stateKey)
            .accessibilityIdentifier("briefing.dig_sheet")
        }
        .sheet(item: $safariItem) { item in
            SafariView(url: item.url)
                .ignoresSafeArea()
        }
    }

    private var fragmentHeader: some View {
        Label(viewModel.fragment ?? "Dig Deeper", systemImage: "magnifyingglass")
            .font(.modalBody.weight(.semibold))
            .foregroundStyle(Color.onSurfaceSecondary)
            .lineLimit(2)
    }

    @ViewBuilder
    private var stateContent: some View {
        switch viewModel.state {
        case .idle:
            EmptyView()
        case .searching:
            loadingRow("Searching the web…")
                .transition(.opacity)
        case .summarizing(let results):
            VStack(alignment: .leading, spacing: 16) {
                loadingRow("Summarizing…")
                resultLinks(results)
            }
            .transition(.opacity)
        case .loaded(let results, let summary):
            VStack(alignment: .leading, spacing: 16) {
                SelectableMarkdownView(
                    markdown: BriefingDigViewModel.citationLinkedMarkdown(summary),
                    adjustsFontForContentSizeCategory: true,
                    linkAction: { citationAction(for: $0, results: results) }
                )
                    .accessibilityIdentifier("briefing.dig_summary")
                resultLinks(results)
            }
            .transition(.opacity)
        case .error(let message):
            VStack(alignment: .leading, spacing: 12) {
                Text(message)
                    .font(.modalBody)
                    .foregroundStyle(Color.statusDestructive)
                Button("Try Again") {
                    viewModel.retry()
                }
                .buttonStyle(.bordered)
                .accessibilityIdentifier("briefing.dig_retry")
            }
            .transition(.opacity)
        }
    }

    private func loadingRow(_ label: String) -> some View {
        HStack(spacing: 10) {
            ProgressView()
            Text(label)
                .font(.modalBody)
                .foregroundStyle(Color.onSurfaceSecondary)
        }
    }

    private func citationAction(
        for url: URL,
        results: [APIBriefingDigSearchResult]
    ) -> UIAction? {
        guard url.scheme == "digsource",
              let index = Int(url.host ?? ""),
              index >= 1,
              index <= results.count,
              let sourceURL = URL(string: results[index - 1].url)
        else { return nil }
        return UIAction { _ in
            safariItem = BriefingSafariItem(url: sourceURL)
        }
    }

    @ViewBuilder
    private func resultLinks(_ results: [APIBriefingDigSearchResult]) -> some View {
        if !results.isEmpty {
            VStack(alignment: .leading, spacing: 10) {
                Text("Sources")
                    .kicker()
                ForEach(Array(results.prefix(5).enumerated()), id: \.offset) { _, result in
                    if let url = URL(string: result.url) {
                        Button {
                            safariItem = BriefingSafariItem(url: url)
                        } label: {
                            Text(result.title)
                                .font(.modalBody.weight(.semibold))
                                .foregroundStyle(Color.brandPrimary)
                                .lineLimit(2)
                                .multilineTextAlignment(.leading)
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
        }
    }
}
struct BriefingSafariItem: Identifiable {
    let url: URL

    var id: String {
        url.absoluteString
    }
}

struct BriefingNarrationChapterSheetItem: Identifiable {
    let lensKey: String
    let episodeGroupID: String

    var id: String { episodeGroupID }
}

struct BriefingNarrationChapterSheet: View {
    @Environment(\.dismiss) private var dismiss

    let narration: BriefingNarration
    let selectedIndex: Int
    let isPreparing: Bool
    let onSelect: (Int) -> Void

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(spacing: 0) {
                    ForEach(
                        Array(narration.chapters.enumerated()),
                        id: \.element.id
                    ) { index, chapter in
                        Button {
                            onSelect(index)
                            dismiss()
                        } label: {
                            chapterRow(chapter, index: index)
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel(chapterAccessibilityLabel(chapter, index: index))
                        .accessibilityIdentifier("briefing.narration.chapter.\(index + 1)")

                        if index < narration.chapters.count - 1 {
                            Divider()
                                .padding(.leading, 52)
                        }
                    }
                }
                .padding(.horizontal, Spacing.appHorizontalMargin)
                .padding(.bottom, 24)
            }
            .background(Color.surfacePrimary)
            .navigationTitle("Chapters")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") {
                        dismiss()
                    }
                }
            }
            .accessibilityIdentifier("briefing.narration.chapter_sheet")
        }
    }

    private func chapterRow(_ chapter: AudioEpisode, index: Int) -> some View {
        HStack(spacing: 12) {
            ZStack {
                Circle()
                    .fill(Color.brandPrimary.opacity(index == selectedIndex ? 0.16 : 0.09))
                    .frame(width: 36, height: 36)

                chapterStatusIcon(chapter, index: index)
            }

            VStack(alignment: .leading, spacing: 3) {
                Text(chapter.title)
                    .font(
                        .modalBody.weight(index == selectedIndex ? .semibold : .regular)
                    )
                    .foregroundStyle(Color.onSurface)

                let detail = chapterDetail(chapter)
                if !detail.isEmpty {
                    Text(detail)
                        .font(.modalSupporting)
                        .foregroundStyle(Color.onSurfaceSecondary)
                }
            }

            Spacer(minLength: 8)

            if index == selectedIndex {
                Image(systemName: "checkmark")
                    .font(.appSymbol(size: 12, weight: .bold))
                    .foregroundStyle(Color.brandPrimary)
            }
        }
        .frame(minHeight: 58)
        .contentShape(Rectangle())
    }

    @ViewBuilder
    private func chapterStatusIcon(_ chapter: AudioEpisode, index: Int) -> some View {
        if isPreparing && index == selectedIndex {
            ProgressView()
                .controlSize(.small)
                .tint(Color.brandPrimary)
        } else {
            Image(systemName: chapterStatusSystemName(chapter))
                .font(.appSymbol(size: 12, weight: .semibold))
                .foregroundStyle(
                    chapter.isFailed ? Color.statusDestructive : Color.brandPrimary
                )
        }
    }

    private func chapterStatusSystemName(_ chapter: AudioEpisode) -> String {
        if chapter.isCompleted {
            return "play.fill"
        }
        if chapter.isFailed {
            return "arrow.clockwise"
        }
        return "clock"
    }

    private func chapterDetail(_ chapter: AudioEpisode) -> String {
        var parts: [String] = []
        if let subtitle = chapter.subtitle, !subtitle.isEmpty {
            parts.append(subtitle)
        }
        if let duration = chapter.durationSeconds, duration > 0 {
            parts.append("~\(max(1, Int((Double(duration) / 60).rounded()))) min")
        }
        if chapter.isGenerating {
            parts.append("preparing")
        } else if chapter.isFailed {
            parts.append("tap to retry")
        }
        return parts.joined(separator: " · ")
    }

    private func chapterAccessibilityLabel(_ chapter: AudioEpisode, index: Int) -> String {
        let detail = chapterDetail(chapter)
        let detailSuffix = detail.isEmpty ? "" : ", \(detail)"
        return "Chapter \(index + 1) of \(narration.chapters.count), \(chapter.title)\(detailSuffix)"
    }
}

/// Keeps deck creation and errors in the briefing's existing creation form.
struct BriefingSourceDeckCreateSheet: View {
    let source: APIBriefingSource
    let viewModel: BriefingSourceActionViewModel
    let focusRecorder: LearningDeckFocusRecorder
    let onCreated: (LearningDeck) -> Void

    @State private var errorMessage: String?

    var body: some View {
        LearningDeckCreateSheet(
            sourceTitle: source.title,
            isSubmitting: viewModel.isCreatingDeck,
            focusRecorder: focusRecorder,
            onCreate: createDeck
        )
        .alert("Couldn’t create deck", isPresented: Binding(
            get: { errorMessage != nil },
            set: { if !$0 { errorMessage = nil } }
        )) {
            Button("OK", role: .cancel) { errorMessage = nil }
        } message: {
            Text(errorMessage ?? "")
        }
        .interactiveDismissDisabled(viewModel.isCreatingDeck)
    }

    private func createDeck(focus: String?) async -> Bool {
        guard let identity = BriefingSourceActionViewModel.knowledgeSource(for: source) else {
            return false
        }
        if let deck = await viewModel.createDeck(source: identity, focus: focus), !Task.isCancelled {
            onCreated(deck)
            return true
        }
        errorMessage = viewModel.errorMessage
        return false
    }
}
