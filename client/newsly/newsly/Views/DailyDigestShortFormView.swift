//
//  DailyDigestShortFormView.swift
//  newsly
//

import SwiftUI
import UIKit

struct DailyDigestShortFormView: View {
    @ObservedObject var viewModel: DailyDigestListViewModel
    let onOpenChatSession: (ChatSessionRoute) -> Void
    @StateObject private var narrationPlaybackService = NarrationPlaybackService.shared
    @State private var loadingNarrationTargets: Set<NarrationTarget> = []
    @State private var activeAlert: ViewAlert?
    @State private var selectedBulletSheet: SelectedBulletSheet?

    private struct ViewAlert: Identifiable {
        let id = UUID()
        let title: String
        let message: String
    }

    private struct SelectedBulletSheet: Identifiable {
        let digest: DailyNewsDigest
        let bullet: DailyNewsDigestBulletDetail
        let bulletIndex: Int

        var id: String {
            "\(digest.id):\(bulletIndex)"
        }
    }

    var body: some View {
        ScrollView {
            LazyVStack(spacing: 0) {
                if case .error(let error) = viewModel.state, viewModel.currentItems().isEmpty {
                    ErrorView(message: error.localizedDescription) {
                        viewModel.refreshTrigger.send(())
                    }
                    .padding(.top, 48)
                    .padding(.horizontal, Spacing.screenHorizontal)
                } else if viewModel.state == .initialLoading, viewModel.currentItems().isEmpty {
                    ProgressView("Loading")
                        .padding(.top, 48)
                } else if viewModel.currentItems().isEmpty {
                    EmptyStateView(
                        icon: "calendar.badge.clock",
                        title: "No Daily Roll-Ups Yet",
                        subtitle: "Daily digest cards will appear once generated."
                    )
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .containerRelativeFrame(.vertical)
                } else {
                    // Large serif header
                    Text("Digest")
                        .font(.terracottaDisplayLarge)
                        .foregroundStyle(Color.onSurface)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, Spacing.screenHorizontal)
                        .padding(.top, 16)
                        .padding(.bottom, 24)

                    // Digest items — each card has its own date/time rule
                    VStack(spacing: 28) {
                        ForEach(viewModel.currentItems()) { digest in
                            let isToday = digest.localDateValue.map { Calendar.current.isDateInToday($0) } ?? false
                            DailyDigestCard(
                                digest: digest,
                                isToday: isToday,
                                isSpeaking: isNarrationActive(for: digest),
                                isLoadingVoice: isNarrationLoading(for: digest),
                                selectedVoicePlaybackSpeedTitle: narrationPlaybackService.playbackSpeedTitle,
                                onToggleRead: { toggleRead(for: digest) },
                                onVoiceSummary: { handleVoiceSummary(for: digest) },
                                onSelectVoicePlaybackSpeed: { option in
                                    handleVoiceSummary(for: digest, rate: option.rate)
                                },
                                onLongPressBullet: { bulletIndex, bullet in
                                    handleBulletLongPress(
                                        digest: digest,
                                        bullet: bullet,
                                        bulletIndex: bulletIndex
                                    )
                                }
                            )
                            .onAppear {
                                if digest.id == viewModel.currentItems().last?.id {
                                    viewModel.loadMoreTrigger.send(())
                                }
                            }
                        }
                    }
                    .padding(.horizontal, Spacing.screenHorizontal)

                    if viewModel.state == .loadingMore {
                        ProgressView()
                            .padding(.vertical, 16)
                    }
                }
            }
            .padding(.vertical, 12)
        }
        .screenContainer()
        .accessibilityIdentifier("short.screen")
        .refreshable {
            viewModel.refreshTrigger.send(())
            await UnreadCountService.shared.refreshCounts()
        }
        .onAppear {
            if viewModel.currentItems().isEmpty {
                viewModel.refreshTrigger.send(())
            }
        }
        .alert(item: $activeAlert) { alert in
            Alert(
                title: Text(alert.title),
                message: Text(alert.message),
                dismissButton: .cancel(Text("OK"))
            )
        }
        .sheet(item: $selectedBulletSheet) { selection in
            DailyDigestBulletDetailSheet(
                digest: selection.digest,
                bullet: selection.bullet,
                shareText: bulletShareText(for: selection.bullet),
                isStartingDigDeeper: viewModel.isStartingDigDeeperChat(
                    digestId: selection.digest.id,
                    bulletIndex: selection.bulletIndex
                ),
                onCopy: { handleCopyBullet(selection) },
                onDigDeeper: { handleBulletDigDeeper(selection) }
            )
        }
    }

    // MARK: - Actions

    private func toggleRead(for digest: DailyNewsDigest) {
        if digest.isRead {
            viewModel.markDigestUnread(id: digest.id)
        } else {
            viewModel.markDigestRead(id: digest.id)
        }
    }

    private func handleVoiceSummary(
        for digest: DailyNewsDigest,
        rate: Float? = nil
    ) {
        let target = narrationTarget(for: digest)
        let playbackRate = rate ?? narrationPlaybackService.playbackRate
        if isNarrationActive(for: digest),
           abs(narrationPlaybackService.playbackRate - playbackRate) < 0.001 {
            narrationPlaybackService.stop()
            return
        }

        loadingNarrationTargets.insert(target)
        Task { @MainActor in
            defer { loadingNarrationTargets.remove(target) }
            do {
                try await narrationPlaybackService.playNarration(
                    for: target,
                    rate: playbackRate,
                    fetchAudio: {
                        try await NarrationService.shared.fetchNarrationAudio(for: target)
                    },
                    fetchNarrationText: {
                        let response = try await NarrationService.shared.fetchNarration(for: target)
                        return response.narrationText
                    }
                )
            } catch {
                activeAlert = ViewAlert(
                    title: "Voice Summary",
                    message: "Failed to load voice summary: \(error.localizedDescription)"
                )
            }
        }
    }

    private func narrationTarget(for digest: DailyNewsDigest) -> NarrationTarget {
        .dailyDigest(digest.id)
    }

    private func isNarrationActive(for digest: DailyNewsDigest) -> Bool {
        narrationPlaybackService.isSpeaking && narrationPlaybackService.speakingTarget == narrationTarget(for: digest)
    }

    private func isNarrationLoading(for digest: DailyNewsDigest) -> Bool {
        loadingNarrationTargets.contains(narrationTarget(for: digest))
    }

    private func handleBulletLongPress(
        digest: DailyNewsDigest,
        bullet: DailyNewsDigestBulletDetail,
        bulletIndex: Int
    ) {
        selectedBulletSheet = SelectedBulletSheet(
            digest: digest,
            bullet: bullet,
            bulletIndex: bulletIndex
        )
    }

    private func handleBulletDigDeeper(_ selection: SelectedBulletSheet) {
        guard !viewModel.isStartingDigDeeperChat(
            digestId: selection.digest.id,
            bulletIndex: selection.bulletIndex
        ) else { return }
        Task { @MainActor in
            do {
                let route = try await viewModel.startBulletDigDeeperChat(
                    digestId: selection.digest.id,
                    bulletIndex: selection.bulletIndex
                )
                selectedBulletSheet = nil
                onOpenChatSession(route)
            } catch {
                activeAlert = ViewAlert(
                    title: "Dig Deeper",
                    message: viewModel.digDeeperError(
                        digestId: selection.digest.id,
                        bulletIndex: selection.bulletIndex
                    ) ?? error.localizedDescription
                )
                viewModel.clearDigDeeperError(
                    digestId: selection.digest.id,
                    bulletIndex: selection.bulletIndex
                )
            }
        }
    }

    private func handleCopyBullet(_ selection: SelectedBulletSheet) {
        UIPasteboard.general.string = bulletShareText(for: selection.bullet)
    }

    private func bulletShareText(for bullet: DailyNewsDigestBulletDetail) -> String {
        let urls = bullet.citations.compactMap(\.url)
        if urls.isEmpty {
            return bullet.cleanedText
        }
        return ([bullet.cleanedText] + urls).joined(separator: "\n\n")
    }
}

// MARK: - Daily Digest Card (Timeline Style)

private struct DailyDigestCard: View {
    let digest: DailyNewsDigest
    let isToday: Bool
    let isSpeaking: Bool
    let isLoadingVoice: Bool
    let selectedVoicePlaybackSpeedTitle: String
    let onToggleRead: () -> Void
    let onVoiceSummary: () -> Void
    let onSelectVoicePlaybackSpeed: (NarrationPlaybackSpeedOption) -> Void
    let onLongPressBullet: (Int, DailyNewsDigestBulletDetail) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            // Date + time with extending rule
            HStack(spacing: 10) {
                HStack(spacing: 5) {
                    Text(shortDateLabel.uppercased())
                        .foregroundStyle(isToday ? Color.terracottaPrimary : Color.onSurfaceSecondary)

                    if !digest.displayTimeLabel.isEmpty {
                        Text("·")
                            .foregroundStyle(Color.onSurfaceSecondary)
                        Text(digest.displayTimeLabel.uppercased())
                            .foregroundStyle(Color.onSurfaceSecondary)
                    }

                    if let coverageLabel = digest.displayCoverageLabel {
                        Text(coverageLabel)
                            .font(.terracottaCategoryPill)
                            .foregroundStyle(Color.terracottaPrimary)
                            .padding(.horizontal, 10)
                            .padding(.vertical, 3)
                            .background(Color.terracottaPrimary.opacity(0.1))
                            .clipShape(Capsule())
                    }
                }
                .font(.terracottaCategoryPill)
                .tracking(1.2)

                Rectangle()
                    .fill(Color.outlineVariant.opacity(0.5))
                    .frame(height: 1)
            }

            // Key points
            if digest.displayBulletDetails.isEmpty {
                Text(digest.cleanedSummary.isEmpty ? "Summary unavailable." : digest.cleanedSummary)
                    .font(.terracottaHeadlineSmall)
                    .foregroundStyle(digest.isRead ? Color.onSurfaceSecondary : Color.onSurface)
                    .lineSpacing(3)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                VStack(alignment: .leading, spacing: 10) {
                    ForEach(Array(digest.displayBulletDetails.enumerated()), id: \.offset) { index, bullet in
                        DigestBulletRow(
                            bullet: bullet,
                            isRead: digest.isRead,
                            onLongPress: { onLongPressBullet(index, bullet) }
                        )
                    }
                }
            }

            if !digest.cleanedSourceLabels.isEmpty {
                Text("From " + digest.cleanedSourceLabels.joined(separator: " · "))
                    .font(.terracottaBodySmall)
                    .foregroundStyle(Color.onSurfaceSecondary)
                    .lineLimit(2)
            }

            // Actions row
            HStack(spacing: 0) {
                if digest.sourceCount > 0 {
                    Text("\(digest.sourceCount) sources")
                        .font(.terracottaBodySmall)
                        .foregroundStyle(Color.onSurfaceSecondary)
                }

                Spacer()

                Button(action: onToggleRead) {
                    Label(
                        digest.isRead ? "Mark Unread" : "Mark Read",
                        systemImage: digest.isRead ? "envelope.badge" : "checkmark.circle"
                    )
                    .font(.terracottaBodySmall)
                    .foregroundStyle(Color.onSurfaceSecondary)
                }
                .buttonStyle(.plain)
                .padding(.trailing, 16)

                NarrationPressButton(
                    isDisabled: isLoadingVoice,
                    accessibilityLabel: isSpeaking
                        ? "Stop narration"
                        : "Play narration at \(selectedVoicePlaybackSpeedTitle)",
                    onTap: onVoiceSummary,
                    onSelectPlaybackSpeed: onSelectVoicePlaybackSpeed
                ) {
                    Group {
                        if isLoadingVoice {
                            ProgressView()
                                .scaleEffect(0.7)
                                .frame(width: 16, height: 16)
                        } else {
                            Label(
                                voiceSummaryButtonTitle,
                                systemImage: isSpeaking ? "speaker.wave.3.fill" : "speaker.wave.2.fill"
                            )
                            .font(.terracottaBodySmall)
                            .lineLimit(1)
                            .minimumScaleFactor(0.85)
                            .foregroundStyle(
                                isSpeaking
                                    ? Color.terracottaPrimary
                                    : Color.onSurfaceSecondary
                            )
                        }
                    }
                }
            }
        }
    }

    private static let shortFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "MMM d"
        f.timeZone = TimeZone.current
        return f
    }()

    private var shortDateLabel: String {
        guard let date = digest.localDateValue else { return digest.localDate }
        let cal = Calendar.current
        if cal.isDateInToday(date) { return "Today" }
        if cal.isDateInYesterday(date) { return "Yesterday" }
        return Self.shortFormatter.string(from: date)
    }

    private var voiceSummaryButtonTitle: String {
        isSpeaking ? "Stop" : "Listen \(selectedVoicePlaybackSpeedTitle)"
    }
}

private struct DigestBulletRow: View {
    let bullet: DailyNewsDigestBulletDetail
    let isRead: Bool
    let onLongPress: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Text("–")
                .font(.terracottaHeadlineSmall)
                .foregroundStyle(Color.onSurfaceSecondary)

            bulletText
                .lineSpacing(3)
                .fixedSize(horizontal: false, vertical: true)
        }
        .contentShape(Rectangle())
        .onLongPressGesture(perform: onLongPress)
    }

    private var bulletText: Text {
        let baseText = Text(bullet.digestPreviewText)
            .font(.terracottaHeadlineSmall)
            .foregroundStyle(isRead ? Color.onSurfaceSecondary : Color.onSurface)

        guard bullet.sourceCount > 0 else {
            return baseText
        }

        let suffix = Text(" \(bullet.sourceCount) \(bullet.sourceCount == 1 ? "source" : "sources")")
            .font(.terracottaBodySmall)
            .foregroundStyle(Color.onSurfaceSecondary)

        return baseText + suffix
    }
}

private struct DailyDigestBulletDetailSheet: View {
    let digest: DailyNewsDigest
    let bullet: DailyNewsDigestBulletDetail
    let shareText: String
    let isStartingDigDeeper: Bool
    let onCopy: () -> Void
    let onDigDeeper: () -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var shareContent: ShareContent?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    Text(bullet.cleanedText)
                        .font(.terracottaHeadlineLarge)
                        .foregroundStyle(Color.onSurface)
                        .fixedSize(horizontal: false, vertical: true)

                    if !bullet.cleanedCommentQuotes.isEmpty {
                        VStack(alignment: .leading, spacing: 12) {
                            Text("Comments")
                                .font(.terracottaCategoryPill)
                                .foregroundStyle(Color.onSurfaceSecondary)

                            ForEach(Array(bullet.cleanedCommentQuotes.enumerated()), id: \.offset) { _, quote in
                                Text(quote)
                                    .font(.terracottaBodyMedium)
                                    .foregroundStyle(Color.onSurfaceSecondary)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                        }
                    }

                    if !bullet.citations.isEmpty {
                        VStack(alignment: .leading, spacing: 12) {
                            Text("Sources")
                                .font(.terracottaCategoryPill)
                                .foregroundStyle(Color.onSurfaceSecondary)

                            ForEach(bullet.citations) { citation in
                                if let urlString = citation.url, let url = URL(string: urlString) {
                                    Link(destination: url) {
                                        VStack(alignment: .leading, spacing: 4) {
                                            Text(citation.label ?? citation.title)
                                                .font(.terracottaBodyMedium)
                                                .foregroundStyle(Color.terracottaPrimary)
                                            if citation.label != nil {
                                                Text(citation.title)
                                                    .font(.terracottaBodySmall)
                                                    .foregroundStyle(Color.onSurfaceSecondary)
                                            }
                                        }
                                        .frame(maxWidth: .infinity, alignment: .leading)
                                    }
                                } else {
                                    Text(citation.title)
                                        .font(.terracottaBodyMedium)
                                        .foregroundStyle(Color.onSurface)
                                }
                            }
                        }
                    }

                    VStack(spacing: 12) {
                        Button(action: onDigDeeper) {
                            HStack {
                                if isStartingDigDeeper {
                                    ProgressView()
                                        .scaleEffect(0.8)
                                } else {
                                    Image(systemName: "brain.head.profile")
                                }
                                Text("Dig Deeper")
                            }
                            .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.borderedProminent)
                        .disabled(isStartingDigDeeper)

                        HStack(spacing: 12) {
                            Button(action: onCopy) {
                                Label("Copy", systemImage: "doc.on.doc")
                                    .frame(maxWidth: .infinity)
                            }
                            .buttonStyle(.bordered)

                            Button {
                                shareContent = ShareContent(
                                    messageContent: shareText,
                                    articleTitle: nil,
                                    articleUrl: nil
                                )
                            } label: {
                                Label("Share", systemImage: "square.and.arrow.up")
                                    .frame(maxWidth: .infinity)
                            }
                            .buttonStyle(.bordered)
                        }
                    }
                }
                .padding(Spacing.screenHorizontal)
                .padding(.vertical, 20)
            }
            .navigationTitle(digest.displayDateLabel)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") {
                        dismiss()
                    }
                }
            }
            .sheet(item: $shareContent) { content in
                ShareSheet(content: content)
            }
        }
    }
}
