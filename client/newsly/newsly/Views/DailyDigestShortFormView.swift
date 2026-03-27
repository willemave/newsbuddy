//
//  DailyDigestShortFormView.swift
//  newsly
//

import SwiftUI

struct DailyDigestShortFormView: View {
    @ObservedObject var viewModel: DailyDigestListViewModel
    let onOpenChatSession: (ChatSessionRoute) -> Void
    @StateObject private var narrationPlaybackService = NarrationPlaybackService.shared
    @State private var loadingNarrationTargets: Set<NarrationTarget> = []
    @State private var activeAlert: ViewAlert?

    private struct ViewAlert: Identifiable {
        let id = UUID()
        let title: String
        let message: String
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
                                isStartingDigDeeper: viewModel.isStartingDigDeeperChat(for: digest.id),
                                selectedVoicePlaybackSpeedTitle: narrationPlaybackService.playbackSpeedTitle,
                                onToggleRead: { toggleRead(for: digest) },
                                onVoiceSummary: { handleVoiceSummary(for: digest) },
                                onSelectVoicePlaybackSpeed: { option in
                                    handleVoiceSummary(for: digest, rate: option.rate)
                                },
                                onDigDeeper: { handleDigDeeper(for: digest) }
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

    private func handleDigDeeper(for digest: DailyNewsDigest) {
        guard !viewModel.isStartingDigDeeperChat(for: digest.id) else { return }

        Task { @MainActor in
            do {
                let route = try await viewModel.startDigDeeperChat(id: digest.id)
                onOpenChatSession(route)
            } catch {
                activeAlert = ViewAlert(
                    title: "Dig Deeper",
                    message: viewModel.digDeeperError(for: digest.id) ?? error.localizedDescription
                )
                viewModel.clearDigDeeperError(for: digest.id)
            }
        }
    }
}

// MARK: - Daily Digest Card (Timeline Style)

private struct DailyDigestCard: View {
    let digest: DailyNewsDigest
    let isToday: Bool
    let isSpeaking: Bool
    let isLoadingVoice: Bool
    let isStartingDigDeeper: Bool
    let selectedVoicePlaybackSpeedTitle: String
    let onToggleRead: () -> Void
    let onVoiceSummary: () -> Void
    let onSelectVoicePlaybackSpeed: (NarrationPlaybackSpeedOption) -> Void
    let onDigDeeper: () -> Void

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
            if digest.cleanedKeyPoints.isEmpty {
                Text(digest.cleanedSummary.isEmpty ? "Summary unavailable." : digest.cleanedSummary)
                    .font(.terracottaHeadlineSmall)
                    .foregroundStyle(digest.isRead ? Color.onSurfaceSecondary : Color.onSurface)
                    .lineSpacing(3)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                VStack(alignment: .leading, spacing: 10) {
                    ForEach(Array(digest.cleanedKeyPoints.enumerated()), id: \.offset) { _, point in
                        HStack(alignment: .firstTextBaseline, spacing: 8) {
                            Text("–")
                                .font(.terracottaHeadlineSmall)
                                .foregroundStyle(Color.onSurfaceSecondary)
                            Text(point)
                                .font(.terracottaHeadlineSmall)
                                .foregroundStyle(digest.isRead ? Color.onSurfaceSecondary : Color.onSurface)
                                .lineSpacing(3)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
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

                if digest.showsDigDeeperAction {
                    Button(action: onDigDeeper) {
                        if isStartingDigDeeper {
                            ProgressView()
                                .scaleEffect(0.7)
                                .frame(width: 16, height: 16)
                        } else {
                            Label("Dig Deeper", systemImage: "brain.head.profile")
                                .font(.terracottaBodySmall)
                                .foregroundStyle(Color.onSurfaceSecondary)
                        }
                    }
                    .buttonStyle(.plain)
                    .disabled(isStartingDigDeeper)
                    .padding(.leading, 16)
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
