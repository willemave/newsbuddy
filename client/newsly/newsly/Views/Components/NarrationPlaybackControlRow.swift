//
//  NarrationPlaybackControlRow.swift
//  newsly
//

import Foundation
import SwiftUI

struct NarrationPlaybackControlRow: View {
    private let playbackService: NarrationPlaybackService
    private let progressState: NarrationPlaybackProgress

    let target: NarrationTarget?
    let isPreparing: Bool
    let cornerRadius: CGFloat
    let onTogglePlayback: () -> Void

    init(
        playbackService: NarrationPlaybackService,
        target: NarrationTarget?,
        isPreparing: Bool,
        cornerRadius: CGFloat = 10,
        onTogglePlayback: @escaping () -> Void
    ) {
        self.playbackService = playbackService
        self.progressState = playbackService.progress
        self.target = target
        self.isPreparing = isPreparing
        self.cornerRadius = cornerRadius
        self.onTogglePlayback = onTogglePlayback
    }

    var body: some View {
        ViewThatFits(in: .horizontal) {
            horizontalControls
            compactControls
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 10)
        .padding(.vertical, 7)
        .background(Color.surfacePrimary.opacity(0.65))
        .clipShape(RoundedRectangle(cornerRadius: cornerRadius, style: .continuous))
    }

    private var horizontalControls: some View {
        HStack(spacing: 10) {
            playbackButton
            speedControls
            Spacer(minLength: 8)
            progressScrubber
                .frame(width: 112)
        }
    }

    private var compactControls: some View {
        VStack(spacing: 6) {
            HStack(spacing: 10) {
                playbackButton
                speedControls
                    .frame(maxWidth: .infinity, alignment: .center)
            }

            progressScrubber
        }
    }

    private var playbackButton: some View {
        Button(action: onTogglePlayback) {
            ZStack {
                Circle()
                    .fill(Color.terracottaPrimary.opacity(0.12))
                    .frame(width: 36, height: 36)

                if isPreparing {
                    ProgressView()
                        .controlSize(.small)
                        .tint(Color.terracottaPrimary)
                } else {
                    Image(systemName: playbackIconName)
                        .font(.appSymbol(size: 12, weight: .bold))
                        .foregroundStyle(Color.terracottaPrimary)
                        .offset(x: playbackIconName == "play.fill" ? 1 : 0)
                }
            }
            .frame(width: 44, height: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(isPreparing)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(playbackAccessibilityLabel)
        .accessibilityAddTraits(.isButton)
    }

    private var speedControls: some View {
        HStack(spacing: 4) {
            ForEach(NarrationPlaybackSpeedOption.standardOptions) { option in
                Button {
                    playbackService.setPlaybackRate(option.rate)
                } label: {
                    Text(option.title)
                        .font(.appCaption2.weight(.semibold))
                        .foregroundStyle(isSelected(option) ? Color.terracottaPrimary : Color.onSurfaceSecondary)
                        .lineLimit(1)
                        .minimumScaleFactor(0.8)
                        // Visible pill matches the 36pt play/pause circle; the
                        // outer frame keeps the full 44pt hit target.
                        .frame(width: 48, height: 36)
                        .background(
                            Capsule()
                                .fill(isSelected(option) ? Color.terracottaPrimary.opacity(0.12) : Color.clear)
                        )
                        .frame(width: 48, height: 44)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityElement(children: .ignore)
                .accessibilityLabel("Playback speed \(option.title)")
                .accessibilityValue(isSelected(option) ? "Selected" : "Not selected")
                .accessibilityAddTraits(.isButton)
            }
        }
    }

    private var progressScrubber: some View {
        PlaybackProgressScrubber(
            progress: progress,
            currentTimeText: formatTime(progressState.currentTime),
            durationText: formatTime(progressState.duration),
            isEnabled: canSeek,
            onSeek: { nextProgress in
                guard let target else { return }
                playbackService.seek(to: nextProgress, for: target)
            }
        )
        .accessibilityLabel("Playback progress")
        .accessibilityValue("\(formatTime(progressState.currentTime)) of \(formatTime(progressState.duration))")
    }

    private var isCurrentTarget: Bool {
        guard let target else { return false }
        return playbackService.speakingTarget == target
    }

    private var canSeek: Bool {
        isCurrentTarget && progressState.duration > 0
    }

    private var progress: Double {
        guard canSeek else { return 0 }
        return min(max(progressState.currentTime / progressState.duration, 0), 1)
    }

    private var playbackIconName: String {
        if isCurrentTarget && playbackService.isSpeaking {
            return "pause.fill"
        }
        return "play.fill"
    }

    private var playbackAccessibilityLabel: String {
        if isPreparing {
            return "Preparing audio"
        }
        if isCurrentTarget && playbackService.isSpeaking {
            return "Pause audio"
        }
        return "Play audio"
    }

    private func isSelected(_ option: NarrationPlaybackSpeedOption) -> Bool {
        abs(playbackService.playbackRate - option.rate) < 0.001
    }

    private func formatTime(_ time: TimeInterval) -> String {
        guard time.isFinite, time > 0 else { return "0:00" }
        let totalSeconds = max(Int(time), 0)
        let minutes = totalSeconds / 60
        let seconds = totalSeconds % 60
        return "\(minutes):\(String(format: "%02d", seconds))"
    }
}

private struct PlaybackProgressScrubber: View {
    let progress: Double
    let currentTimeText: String
    let durationText: String
    let isEnabled: Bool
    let onSeek: (Double) -> Void

    var body: some View {
        VStack(spacing: 1) {
            GeometryReader { geometry in
                let width = max(geometry.size.width, 1)
                let clampedProgress = min(max(progress, 0), 1)
                let thumbX = width * clampedProgress
                ZStack(alignment: .leading) {
                    Capsule()
                        .fill(Color.outlineVariant.opacity(0.28))
                        .frame(height: 3)
                        .position(x: width / 2, y: 8)

                    Capsule()
                        .fill(Color.terracottaPrimary.opacity(0.8))
                        .frame(width: max(thumbX, 0), height: 3)
                        .position(x: max(thumbX / 2, 0), y: 8)

                    Circle()
                        .fill(isEnabled ? Color.terracottaPrimary : Color.onSurfaceSecondary.opacity(0.45))
                        .frame(width: 8, height: 8)
                        .position(x: thumbX, y: 8)
                }
                .contentShape(Rectangle())
                .gesture(
                    DragGesture(minimumDistance: 0)
                        .onChanged { value in
                            guard isEnabled else { return }
                            let nextProgress = min(max(value.location.x / width, 0), 1)
                            onSeek(nextProgress)
                        }
                )
            }
            .frame(height: 16)

            HStack {
                Text(currentTimeText)
                Spacer(minLength: 6)
                Text(durationText)
            }
            .font(.appSans(size: 10, weight: .medium).monospacedDigit())
            .foregroundStyle(Color.onSurfaceSecondary.opacity(0.75))
        }
        .opacity(isEnabled ? 1 : 0.55)
        .accessibilityElement(children: .ignore)
    }
}
