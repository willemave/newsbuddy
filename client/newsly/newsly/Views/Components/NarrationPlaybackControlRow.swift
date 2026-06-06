//
//  NarrationPlaybackControlRow.swift
//  newsly
//

import Foundation
import SwiftUI

struct NarrationPlaybackControlRow: View {
    @ObservedObject private var playbackService: NarrationPlaybackService

    let target: NarrationTarget?
    let isPreparing: Bool
    let onTogglePlayback: () -> Void

    init(
        playbackService: NarrationPlaybackService,
        target: NarrationTarget?,
        isPreparing: Bool,
        onTogglePlayback: @escaping () -> Void
    ) {
        self._playbackService = ObservedObject(wrappedValue: playbackService)
        self.target = target
        self.isPreparing = isPreparing
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
        .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
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
                Spacer(minLength: 0)
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
                        .font(.system(size: 12, weight: .bold))
                        .foregroundStyle(Color.terracottaPrimary)
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
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(isSelected(option) ? Color.terracottaPrimary : Color.onSurfaceSecondary)
                        .lineLimit(1)
                        .minimumScaleFactor(0.8)
                        .frame(width: 48, height: 44)
                        .background(
                            Capsule()
                                .fill(isSelected(option) ? Color.terracottaPrimary.opacity(0.12) : Color.clear)
                        )
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityElement(children: .ignore)
                .accessibilityLabel("Playback speed \(option.title)")
                .accessibilityValue(isSelected(option) ? "Selected" : "")
                .accessibilityAddTraits(.isButton)
            }
        }
    }

    private var progressScrubber: some View {
        PlaybackProgressScrubber(
            progress: progress,
            currentTimeText: formatTime(playbackService.currentTime),
            durationText: formatTime(playbackService.duration),
            isEnabled: canSeek,
            onSeek: { nextProgress in
                guard let target else { return }
                playbackService.seek(to: nextProgress, for: target)
            }
        )
        .accessibilityLabel("Playback progress")
        .accessibilityValue("\(formatTime(playbackService.currentTime)) of \(formatTime(playbackService.duration))")
    }

    private var isCurrentTarget: Bool {
        guard let target else { return false }
        return playbackService.speakingTarget == target
    }

    private var canSeek: Bool {
        isCurrentTarget && playbackService.duration > 0
    }

    private var progress: Double {
        guard canSeek else { return 0 }
        return min(max(playbackService.currentTime / playbackService.duration, 0), 1)
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
                let labelX = width > 36 ? min(max(thumbX, 18), width - 18) : width / 2

                ZStack(alignment: .leading) {
                    Capsule()
                        .fill(Color.outlineVariant.opacity(0.28))
                        .frame(height: 3)
                        .position(x: width / 2, y: 19)

                    Capsule()
                        .fill(Color.terracottaPrimary.opacity(0.8))
                        .frame(width: max(thumbX, 0), height: 3)
                        .position(x: max(thumbX / 2, 0), y: 19)

                    Text(currentTimeText)
                        .font(.system(size: 8, weight: .semibold, design: .monospaced))
                        .foregroundStyle(Color.onSurfaceSecondary)
                        .padding(.horizontal, 4)
                        .padding(.vertical, 1)
                        .background(
                            Capsule()
                                .fill(Color.surfaceSecondary.opacity(0.95))
                        )
                        .position(x: labelX, y: 6)

                    Circle()
                        .fill(isEnabled ? Color.terracottaPrimary : Color.onSurfaceSecondary.opacity(0.45))
                        .frame(width: 8, height: 8)
                        .position(x: thumbX, y: 19)
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
            .frame(height: 25)

            HStack {
                Text("0:00")
                Spacer(minLength: 6)
                Text(durationText)
            }
            .font(.system(size: 8, weight: .medium, design: .monospaced))
            .foregroundStyle(Color.onSurfaceSecondary.opacity(0.75))
        }
        .opacity(isEnabled ? 1 : 0.55)
        .accessibilityElement(children: .ignore)
    }
}
