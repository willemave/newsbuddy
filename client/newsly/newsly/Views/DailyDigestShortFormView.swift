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
        let bulletId: Int

        var id: String {
            "\(digest.id):\(bulletId)"
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
                                onLongPressBullet: { bulletId, bullet in
                                    handleBulletLongPress(
                                        digest: digest,
                                        bullet: bullet,
                                        bulletId: bulletId
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
                    bulletId: selection.bulletId
                ),
                onCopy: { handleCopyBullet(selection) },
                onDigDeeper: { handleBulletDigDeeper(selection) }
            )
        }
    }

    // MARK: - Actions

    private func toggleRead(for digest: DailyNewsDigest) {
        guard !digest.isRead else { return }
        viewModel.markDigestRead(id: digest.id)
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
        bulletId: Int
    ) {
        selectedBulletSheet = SelectedBulletSheet(
            digest: digest,
            bullet: bullet,
            bulletId: bulletId
        )
    }

    private func handleBulletDigDeeper(_ selection: SelectedBulletSheet) {
        guard !viewModel.isStartingDigDeeperChat(
            digestId: selection.digest.id,
            bulletId: selection.bulletId
        ) else { return }
        Task { @MainActor in
            do {
                let route = try await viewModel.startBulletDigDeeperChat(
                    digestId: selection.digest.id,
                    bulletId: selection.bulletId
                )
                selectedBulletSheet = nil
                onOpenChatSession(route)
            } catch {
                activeAlert = ViewAlert(
                    title: "Dig Deeper",
                    message: viewModel.digDeeperError(
                        digestId: selection.digest.id,
                        bulletId: selection.bulletId
                    ) ?? error.localizedDescription
                )
                viewModel.clearDigDeeperError(
                    digestId: selection.digest.id,
                    bulletId: selection.bulletId
                )
            }
        }
    }

    private func handleCopyBullet(_ selection: SelectedBulletSheet) {
        UIPasteboard.general.string = bulletShareText(for: selection.bullet)
    }

    private func bulletShareText(for bullet: DailyNewsDigestBulletDetail) -> String {
        let urls = bullet.citations.compactMap(\.effectiveURL)
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
        VStack(alignment: .leading, spacing: 16) {
            VStack(alignment: .leading, spacing: 8) {
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
                    }
                    .font(.terracottaCategoryPill)
                    .tracking(1.2)

                    Rectangle()
                        .fill(Color.outlineVariant.opacity(0.5))
                        .frame(height: 1)
                }

                if !headerTitle.isEmpty {
                    Text(headerTitle)
                        .font(.terracottaBodyMedium.weight(.semibold))
                        .foregroundStyle(digest.isRead ? Color.onSurfaceSecondary : Color.onSurface)
                        .lineLimit(2)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }

            // Key points
            if digest.displayBulletDetails.isEmpty {
                Text(digest.cleanedSummary.isEmpty ? "Summary unavailable." : digest.cleanedSummary)
                    .font(.terracottaHeadlineSmall)
                    .foregroundStyle(digest.isRead ? Color.onSurfaceSecondary : Color.onSurface)
                    .lineSpacing(3)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                VStack(alignment: .leading, spacing: 2) {
                            ForEach(digest.displayBulletDetails) { bullet in
                                DigestBulletRow(
                                    bullet: bullet,
                                    isRead: digest.isRead,
                                    onLongPress: { onLongPressBullet(bullet.id, bullet) }
                                )
                            }
                }
                .padding(.horizontal, -8) // offset bullet row's internal padding
            }

            if !digest.cleanedSourceLabels.isEmpty {
                Text(digest.cleanedSourceLabels.joined(separator: " · "))
                    .font(.terracottaCategoryPill)
                    .foregroundStyle(Color.onSurfaceSecondary.opacity(0.7))
                    .tracking(0.3)
                    .lineLimit(2)
            }

            // Actions row
            HStack(spacing: 0) {
                Spacer()

                Button(action: onToggleRead) {
                    Image(systemName: digest.isRead ? "envelope.badge" : "checkmark.circle")
                        .font(.system(size: 14))
                        .foregroundStyle(Color.onSurfaceSecondary.opacity(0.6))
                }
                .buttonStyle(.plain)
                .padding(.trailing, 20)

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
                            Image(systemName: isSpeaking ? "speaker.wave.3.fill" : "speaker.wave.2.fill")
                                .font(.system(size: 14))
                                .foregroundStyle(
                                    isSpeaking
                                        ? Color.terracottaPrimary
                                        : Color.onSurfaceSecondary.opacity(0.6)
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

    private var headerTitle: String {
        digest.title.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private var voiceSummaryButtonTitle: String {
        isSpeaking ? "Stop" : "Listen \(selectedVoicePlaybackSpeedTitle)"
    }
}

private struct DigestBulletRow: View {
    let bullet: DailyNewsDigestBulletDetail
    let isRead: Bool
    let onLongPress: () -> Void

    @State private var isPressed = false

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Circle()
                .fill(isRead ? Color.onSurfaceSecondary.opacity(0.4) : Color.terracottaPrimary.opacity(0.6))
                .frame(width: 5, height: 5)
                .padding(.top, 9)

            VStack(alignment: .leading, spacing: 4) {
                bulletText
                    .lineSpacing(3)
                    .fixedSize(horizontal: false, vertical: true)

                if bullet.sourceCount > 0 {
                    Text("\(bullet.sourceCount) \(bullet.sourceCount == 1 ? "source" : "sources")")
                        .font(.terracottaCategoryPill)
                        .foregroundStyle(Color.onSurfaceSecondary.opacity(0.7))
                        .tracking(0.4)
                }
            }
        }
        .padding(.vertical, 6)
        .padding(.horizontal, 8)
        .background(
            RoundedRectangle(cornerRadius: 8)
                .fill(isPressed ? Color.surfaceContainer.opacity(0.6) : Color.clear)
        )
        .contentShape(Rectangle())
        .onLongPressGesture(
            minimumDuration: 0.4,
            pressing: { pressing in
                withAnimation(.easeInOut(duration: 0.15)) {
                    isPressed = pressing
                }
            },
            perform: onLongPress
        )
    }

    private var bulletText: Text {
        Text(bullet.digestPreviewText)
            .font(.terracottaHeadlineSmall)
            .foregroundStyle(isRead ? Color.onSurfaceSecondary : Color.onSurface)
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
    @State private var showAllSources = false

    private let sourcePreviewLimit = 5

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    // Bullet headline — use preview text to avoid repeating
                    // comment quotes that are shown separately below.
                    Text(bullet.digestPreviewText)
                        .font(.terracottaHeadlineLarge)
                        .foregroundStyle(Color.onSurface)
                        .lineSpacing(4)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.bottom, bullet.cleanedCommentQuotes.isEmpty && bullet.citations.isEmpty ? 24 : 28)

                    // Comments section
                    if !bullet.cleanedCommentQuotes.isEmpty {
                        VStack(alignment: .leading, spacing: 16) {
                            Text("DISCUSSION")
                                .font(.terracottaCategoryPill)
                                .foregroundStyle(Color.onSurfaceSecondary.opacity(0.6))
                                .tracking(1.0)

                            VStack(alignment: .leading, spacing: 12) {
                                ForEach(Array(bullet.cleanedCommentQuotes.enumerated()), id: \.offset) { _, quote in
                                    HStack(alignment: .top, spacing: 12) {
                                        RoundedRectangle(cornerRadius: 1)
                                            .fill(Color.outlineVariant)
                                            .frame(width: 2)

                                        Text(quote)
                                            .font(.terracottaBodyMedium)
                                            .foregroundStyle(Color.onSurfaceSecondary)
                                            .lineSpacing(3)
                                            .fixedSize(horizontal: false, vertical: true)
                                    }
                                }
                            }
                        }
                        .padding(.bottom, bullet.citations.isEmpty ? 24 : 28)
                    }

                    // Sources section
                    if !bullet.citations.isEmpty {
                        let visibleCitations = showAllSources
                            ? bullet.citations
                            : Array(bullet.citations.prefix(sourcePreviewLimit))
                        let hasOverflow = bullet.citations.count > sourcePreviewLimit

                        VStack(alignment: .leading, spacing: 16) {
                            Text("SOURCES")
                                .font(.terracottaCategoryPill)
                                .foregroundStyle(Color.onSurfaceSecondary.opacity(0.6))
                                .tracking(1.0)

                            VStack(alignment: .leading, spacing: 10) {
                                ForEach(visibleCitations) { citation in
                                    if let urlString = citation.effectiveURL, let url = URL(string: urlString) {
                                        Link(destination: url) {
                                            HStack(alignment: .top, spacing: 8) {
                                                Image(systemName: "arrow.up.right")
                                                    .font(.system(size: 10, weight: .semibold))
                                                    .foregroundStyle(Color.terracottaPrimary.opacity(0.6))
                                                    .padding(.top, 3)

                                                VStack(alignment: .leading, spacing: 2) {
                                                    Text(citation.label ?? citation.title)
                                                        .font(.terracottaBodyMedium)
                                                        .foregroundStyle(Color.terracottaPrimary)
                                                        .lineLimit(2)
                                                    if citation.label != nil {
                                                        Text(citation.title)
                                                            .font(.terracottaBodySmall)
                                                            .foregroundStyle(Color.onSurfaceSecondary.opacity(0.7))
                                                            .lineLimit(1)
                                                    }
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

                                if hasOverflow && !showAllSources {
                                    Button {
                                        withAnimation(.easeInOut(duration: 0.2)) {
                                            showAllSources = true
                                        }
                                    } label: {
                                        Text("Show all \(bullet.citations.count) sources")
                                            .font(.terracottaBodySmall)
                                            .foregroundStyle(Color.terracottaPrimary)
                                    }
                                    .buttonStyle(.plain)
                                    .padding(.top, 4)
                                }
                            }
                        }
                        .padding(.bottom, 24)
                    }

                    // Separator before actions
                    Rectangle()
                        .fill(Color.outlineVariant.opacity(0.3))
                        .frame(height: 1)
                        .padding(.bottom, 16)

                    // Actions — compact row
                    HStack(spacing: 16) {
                        Button(action: onDigDeeper) {
                            HStack(spacing: 6) {
                                if isStartingDigDeeper {
                                    ProgressView()
                                        .scaleEffect(0.7)
                                        .frame(width: 14, height: 14)
                                } else {
                                    Image(systemName: "brain.head.profile")
                                        .font(.system(size: 13))
                                }
                                Text("Dig Deeper")
                                    .font(.terracottaBodyMedium)
                            }
                            .foregroundStyle(Color.terracottaPrimary)
                        }
                        .buttonStyle(.plain)
                        .disabled(isStartingDigDeeper)

                        Spacer()

                        Button(action: onCopy) {
                            Image(systemName: "doc.on.doc")
                                .font(.system(size: 14))
                                .foregroundStyle(Color.onSurfaceSecondary)
                        }
                        .buttonStyle(.plain)

                        Button {
                            shareContent = ShareContent(
                                messageContent: shareText,
                                articleTitle: nil,
                                articleUrl: nil
                            )
                        } label: {
                            Image(systemName: "square.and.arrow.up")
                                .font(.system(size: 14))
                                .foregroundStyle(Color.onSurfaceSecondary)
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding(.horizontal, Spacing.screenHorizontal)
                .padding(.top, 24)
                .padding(.bottom, 20)
            }
            .navigationTitle(digest.displayDateLabel)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") {
                        dismiss()
                    }
                    .font(.terracottaBodyMedium)
                }
            }
            .sheet(item: $shareContent) { content in
                ShareSheet(content: content)
            }
        }
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
        .presentationCornerRadius(24)
    }
}
