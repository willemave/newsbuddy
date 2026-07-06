//
//  PodcastAudioPromptCard.swift
//  newsly
//

import SwiftUI

struct PodcastAudioPromptCard: View {
    let isLoading: Bool
    let isActive: Bool
    let statusText: String
    let accessibilityLabel: String
    let onTap: () -> Void
    let onSelectPlaybackSpeed: (NarrationPlaybackSpeedOption) -> Void

    var body: some View {
        NarrationPressButton(
            isDisabled: isLoading,
            accessibilityLabel: accessibilityLabel,
            onTap: onTap,
            onSelectPlaybackSpeed: onSelectPlaybackSpeed
        ) {
            HStack(spacing: 12) {
                ChatSheetIcon(
                    isActive ? "pause.fill" : "person.3.sequence.fill",
                    color: .readerBodyText
                )

                VStack(alignment: .leading, spacing: 3) {
                    Text(isActive ? "Pause podcast overview" : "Podcast overview")
                        .font(.appSubheadline)
                        .fontWeight(.semibold)
                        .foregroundColor(Color.onSurface)
                    Text(statusText)
                        .font(.appCaption)
                        .foregroundColor(Color.onSurfaceSecondary)
                        .lineLimit(1)
                        .minimumScaleFactor(0.85)
                }

                Spacer()

                if isLoading {
                    ProgressView()
                        .controlSize(.small)
                } else {
                    Image(systemName: "chevron.right")
                        .font(.appCaption.weight(.semibold))
                        .foregroundColor(Color.onSurfaceTertiary)
                }
            }
            .chatWideActionSurface()
        }
        .accessibilityIdentifier("content.audio.podcast_overview")
    }
}
