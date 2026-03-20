//
//  DailyDigestShortFormView.swift
//  newsly
//

import SwiftUI

struct DailyDigestShortFormView: View {
    @ObservedObject var viewModel: DailyDigestListViewModel
    let onOpenChatSession: (ChatSessionRoute) -> Void
    @StateObject private var narrationService = DigestNarrationService.shared
    @AppStorage("daily_digest_narration_playback_rate") private var narrationPlaybackRate = 1.0
    @State private var loadingVoiceDigestIds: Set<Int> = []
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
                    ForEach(viewModel.currentItems()) { digest in
                        DailyDigestCard(
                            digest: digest,
                            isSpeaking: narrationService.isSpeaking && narrationService.speakingDigestId == digest.id,
                            isLoadingVoice: loadingVoiceDigestIds.contains(digest.id),
                            isStartingDigDeeper: viewModel.isStartingDigDeeperChat(for: digest.id),
                            playbackRate: narrationPlaybackRate,
                            onSelectPlaybackRate: { rate in
                                narrationPlaybackRate = rate
                                narrationService.setPlaybackRate(Float(rate))
                            },
                            onToggleRead: { toggleRead(for: digest) },
                            onVoiceSummary: { handleVoiceSummary(for: digest) },
                            onDigDeeper: { handleDigDeeper(for: digest) }
                        )
                        .onAppear {
                            if digest.id == viewModel.currentItems().last?.id {
                                viewModel.loadMoreTrigger.send(())
                            }
                        }
                    }

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

    private func toggleRead(for digest: DailyNewsDigest) {
        if digest.isRead {
            viewModel.markDigestUnread(id: digest.id)
        } else {
            viewModel.markDigestRead(id: digest.id)
        }
    }

    private func handleVoiceSummary(for digest: DailyNewsDigest) {
        narrationService.setPlaybackRate(Float(narrationPlaybackRate))
        if narrationService.isSpeaking && narrationService.speakingDigestId == digest.id {
            narrationService.stop()
            return
        }

        loadingVoiceDigestIds.insert(digest.id)
        Task {
            defer { loadingVoiceDigestIds.remove(digest.id) }
            do {
                if narrationService.playCachedAudio(for: digest.id) {
                    return
                }

                let audioData = try await viewModel.fetchVoiceSummaryAudio(id: digest.id)
                try narrationService.playAudio(audioData, digestId: digest.id)
            } catch {
                do {
                let response = try await viewModel.fetchVoiceSummary(id: digest.id)
                narrationService.speak(text: response.narrationText, digestId: digest.id)
            } catch {
                activeAlert = ViewAlert(
                    title: "Voice Summary",
                    message: "Failed to load voice summary: \(error.localizedDescription)"
                )
            }
        }
    }
    }

    private func handleDigDeeper(for digest: DailyNewsDigest) {
        guard !viewModel.isStartingDigDeeperChat(for: digest.id) else { return }

        Task {
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

// MARK: - Daily Digest Card

private struct DailyDigestCard: View {
    let digest: DailyNewsDigest
    let isSpeaking: Bool
    let isLoadingVoice: Bool
    let isStartingDigDeeper: Bool
    let playbackRate: Double
    let onSelectPlaybackRate: (Double) -> Void
    let onToggleRead: () -> Void
    let onVoiceSummary: () -> Void
    let onDigDeeper: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Date header
            HStack(alignment: .firstTextBaseline) {
                Text(digest.displayDateLabel.uppercased())
                    .font(.system(size: 16, weight: .bold))
                    .tracking(0.8)
                    .foregroundStyle(digest.isRead ? Color.textTertiary : Color.textPrimary)

                Spacer()

                if !digest.isRead {
                    Circle()
                        .fill(Color.accentColor)
                        .frame(width: 7, height: 7)
                }
            }
            .padding(.bottom, 14)

            if let coverageLabel = digest.displayCoverageLabel {
                Text(coverageLabel)
                    .font(.caption)
                    .foregroundStyle(Color.textSecondary)
                    .padding(.bottom, 14)
            }

            // Key points
            VStack(alignment: .leading, spacing: 12) {
                if digest.cleanedKeyPoints.isEmpty {
                    Text(digest.cleanedSummary.isEmpty ? "Summary unavailable." : digest.cleanedSummary)
                        .font(.subheadline)
                        .foregroundStyle(digest.isRead ? Color.textSecondary : .primary)
                        .lineSpacing(3)
                        .fixedSize(horizontal: false, vertical: true)
                } else {
                    ForEach(Array(digest.cleanedKeyPoints.enumerated()), id: \.offset) { _, point in
                        HStack(alignment: .top, spacing: 10) {
                            Text("–")
                                .font(.subheadline.weight(.medium))
                                .foregroundStyle(Color.textSecondary)
                            Text(point)
                                .font(.subheadline)
                                .foregroundStyle(digest.isRead ? Color.textSecondary : .primary)
                                .lineSpacing(3)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
            }
            .padding(.bottom, 16)

            // Actions row
            HStack(spacing: 0) {
                if digest.sourceCount > 0 {
                    Text("\(digest.sourceCount) sources")
                        .font(.feedMeta)
                        .foregroundStyle(Color.textSecondary)
                }

                Spacer()

                Button(action: onToggleRead) {
                    Label(
                        digest.isRead ? "Mark Unread" : "Mark Read",
                        systemImage: digest.isRead ? "envelope.badge" : "checkmark.circle"
                    )
                    .font(.caption.weight(.medium))
                    .foregroundStyle(Color.textSecondary)
                }
                .buttonStyle(.plain)
                .padding(.trailing, 16)

                Button(action: onVoiceSummary) {
                    if isLoadingVoice {
                        ProgressView()
                            .scaleEffect(0.7)
                            .frame(width: 16, height: 16)
                    } else {
                        Label(
                            voiceSummaryButtonTitle,
                            systemImage: isSpeaking ? "speaker.slash.fill" : "speaker.wave.2.fill"
                        )
                        .font(.caption.weight(.medium))
                        .foregroundStyle(isSpeaking ? Color.accentColor : Color.textSecondary)
                    }
                }
                .contextMenu {
                    ForEach(DigestNarrationService.supportedPlaybackRates, id: \.self) { rate in
                        Button {
                            onSelectPlaybackRate(Double(rate))
                        } label: {
                            if abs(Double(rate) - playbackRate) < 0.001 {
                                Label(playbackRateLabel(for: Double(rate)), systemImage: "checkmark")
                            } else {
                                Text(playbackRateLabel(for: Double(rate)))
                            }
                        }
                    }
                }
                .buttonStyle(.plain)
                .disabled(isLoadingVoice)

                if digest.showsDigDeeperAction {
                    Button(action: onDigDeeper) {
                        if isStartingDigDeeper {
                            ProgressView()
                                .scaleEffect(0.7)
                                .frame(width: 16, height: 16)
                        } else {
                            Label("Dig Deeper", systemImage: "brain.head.profile")
                                .font(.caption.weight(.medium))
                                .foregroundStyle(Color.textSecondary)
                        }
                    }
                    .buttonStyle(.plain)
                    .disabled(isStartingDigDeeper)
                    .padding(.leading, 16)
                }
            }
        }
        .padding(.horizontal, Spacing.rowHorizontal)
        .padding(.vertical, 20)
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(Color.borderSubtle.opacity(0.4))
                .frame(height: 6)
        }
    }

    private var voiceSummaryButtonTitle: String {
        let action = isSpeaking ? "Stop" : "Listen"
        return "\(action) \(playbackRateLabel(for: playbackRate))"
    }

    private func playbackRateLabel(for rate: Double) -> String {
        "\(rate.formatted(.number.precision(.fractionLength(0...2))))x"
    }
}
