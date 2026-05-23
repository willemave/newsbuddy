//
//  CustomNarrationListSheet.swift
//  newsly
//

import SwiftUI

struct CustomNarrationListSheet: View {
    @ObservedObject var viewModel: CustomNarrationLibraryViewModel
    @ObservedObject var playbackService: NarrationPlaybackService

    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                if let errorMessage = viewModel.errorMessage {
                    Text(errorMessage)
                        .font(.terracottaBodySmall)
                        .foregroundStyle(.red)
                        .appListRow()
                }

                if viewModel.isLoading && viewModel.episodes.isEmpty {
                    loadingRow
                        .appListRow()
                } else if viewModel.episodes.isEmpty {
                    emptyRow
                        .appListRow()
                } else {
                    ForEach(viewModel.episodes) { episode in
                        narrationRow(episode)
                            .appListRow()
                    }
                }
            }
            .listStyle(.plain)
            .scrollContentBackground(.hidden)
            .background(Color.surfacePrimary)
            .navigationTitle("Narration")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") {
                        dismiss()
                    }
                }
            }
            .refreshable {
                await viewModel.load()
            }
        }
    }

    private var loadingRow: some View {
        HStack(spacing: 10) {
            ProgressView()
                .controlSize(.small)
            Text("Loading narrations")
                .font(.terracottaBodyMedium)
                .foregroundStyle(Color.onSurfaceSecondary)
        }
    }

    private var emptyRow: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("No narrations yet")
                .font(.terracottaHeadlineSmall)
                .foregroundStyle(Color.onSurface)
            Text("Created narrations will show up here.")
                .font(.terracottaBodySmall)
                .foregroundStyle(Color.onSurfaceSecondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, 10)
    }

    private func narrationRow(_ episode: AudioEpisode) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Button {
                Task {
                    await viewModel.handleTap(episode)
                }
            } label: {
                HStack(spacing: 12) {
                    ZStack {
                        RoundedRectangle(cornerRadius: 10, style: .continuous)
                            .fill(Color.terracottaPrimary.opacity(0.14))
                            .frame(width: 38, height: 38)

                        narrationIcon(episode)
                    }

                    VStack(alignment: .leading, spacing: 3) {
                        Text(episode.title)
                            .font(.terracottaBodyLarge.weight(.semibold))
                            .foregroundStyle(Color.onSurface)
                            .lineLimit(2)

                        Text(viewModel.subtitle(for: episode))
                            .font(.terracottaBodySmall)
                            .foregroundStyle(Color.onSurfaceSecondary)
                            .lineLimit(1)
                    }

                    Spacer(minLength: 10)

                    Image(systemName: viewModel.isPlaying(episode) ? "pause.fill" : "play.fill")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(episode.isCompleted ? Color.terracottaPrimary : Color.onSurfaceSecondary)
                        .frame(width: 30, height: 30)
                        .background(Color.surfaceSecondary)
                        .clipShape(Circle())
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            if episode.isCompleted && viewModel.isPlaying(episode) {
                NarrationPlaybackControlRow(
                    playbackService: playbackService,
                    target: .audioEpisode(episode.id),
                    isPreparing: false,
                    onTogglePlayback: {
                        Task {
                            await viewModel.handleTap(episode)
                        }
                    }
                )
            }
        }
        .padding(.horizontal, Spacing.rowHorizontal)
        .padding(.vertical, 9)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("knowledge.narration.\(episode.id)")
    }

    @ViewBuilder
    private func narrationIcon(_ episode: AudioEpisode) -> some View {
        if episode.isGenerating {
            ProgressView()
                .controlSize(.small)
        } else if episode.isFailed {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(.red)
        } else {
            Image(systemName: viewModel.isPlaying(episode) ? "speaker.wave.3.fill" : "waveform")
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(Color.terracottaPrimary)
        }
    }
}
