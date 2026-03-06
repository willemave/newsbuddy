//
//  DailyDigestShortFormView.swift
//  newsly
//

import SwiftUI

struct DailyDigestShortFormView: View {
    @ObservedObject var viewModel: DailyDigestListViewModel
    @StateObject private var narrationService = DigestNarrationService.shared
    @State private var loadingVoiceDigestIds: Set<Int> = []
    @State private var voiceErrorMessage: String?

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
                            onToggleRead: { toggleRead(for: digest) },
                            onVoiceSummary: { handleVoiceSummary(for: digest) }
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
        .alert("Voice Summary", isPresented: Binding(
            get: { voiceErrorMessage != nil },
            set: { newValue in
                if !newValue {
                    voiceErrorMessage = nil
                }
            }
        )) {
            Button("OK", role: .cancel) { voiceErrorMessage = nil }
        } message: {
            Text(voiceErrorMessage ?? "")
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
        if narrationService.isSpeaking && narrationService.speakingDigestId == digest.id {
            narrationService.stop()
            return
        }

        loadingVoiceDigestIds.insert(digest.id)
        Task {
            defer { loadingVoiceDigestIds.remove(digest.id) }
            do {
                let response = try await viewModel.fetchVoiceSummary(id: digest.id)
                narrationService.speak(text: response.narrationText, digestId: digest.id)
            } catch {
                voiceErrorMessage = "Failed to load voice summary: \(error.localizedDescription)"
            }
        }
    }
}

// MARK: - Daily Digest Card

private struct DailyDigestCard: View {
    let digest: DailyNewsDigest
    let isSpeaking: Bool
    let isLoadingVoice: Bool
    let onToggleRead: () -> Void
    let onVoiceSummary: () -> Void

    private var pointColor: Color {
        digest.isRead ? .textSecondary : .textPrimary
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Date header
            HStack(alignment: .firstTextBaseline) {
                Text(digest.displayDateLabel.uppercased())
                    .font(.system(size: 12, weight: .bold))
                    .tracking(1.0)
                    .foregroundStyle(digest.isRead ? Color.textTertiary : Color.sectionDelimiter)

                Spacer()

                if !digest.isRead {
                    Circle()
                        .fill(Color.accentColor)
                        .frame(width: 7, height: 7)
                }
            }
            .padding(.bottom, 14)

            // Key points
            VStack(alignment: .leading, spacing: 12) {
                ForEach(Array(digest.keyPoints.enumerated()), id: \.offset) { _, point in
                    HStack(alignment: .top, spacing: 10) {
                        Text("–")
                            .font(.subheadline.weight(.medium))
                            .foregroundStyle(Color.textTertiary)
                        Text(point)
                            .font(.subheadline)
                            .foregroundStyle(pointColor)
                            .lineSpacing(3)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
            .padding(.bottom, 16)

            // Actions row
            HStack(spacing: 0) {
                if digest.sourceCount > 0 {
                    Text("\(digest.sourceCount) sources")
                        .font(.feedMeta)
                        .foregroundStyle(Color.textTertiary)
                }

                Spacer()

                Button(action: onToggleRead) {
                    Label(
                        digest.isRead ? "Mark Unread" : "Mark Read",
                        systemImage: digest.isRead ? "envelope.badge" : "checkmark.circle"
                    )
                    .font(.caption.weight(.medium))
                    .foregroundStyle(Color.textTertiary)
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
                            isSpeaking ? "Stop" : "Listen",
                            systemImage: isSpeaking ? "speaker.slash.fill" : "speaker.wave.2.fill"
                        )
                        .font(.caption.weight(.medium))
                        .foregroundStyle(isSpeaking ? Color.accentColor : Color.textTertiary)
                    }
                }
                .buttonStyle(.plain)
                .disabled(isLoadingVoice)
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
}
