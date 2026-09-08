//
//  NarrationPlaybackControlRow.swift
//  newsly
//
//  Shared playback vocabulary: one filled play button, a single speed menu,
//  and a scrubber. The briefing player and the podcast detail row compose
//  these so audio looks the same everywhere it appears.
//

import Foundation
import SwiftUI

/// Plain values a playback surface renders from, resolved once from the
/// service for a specific target so views never reach into the service.
struct NarrationPlaybackSnapshot: Equatable {
    var isPlaying = false
    var isPreparing = false
    var canSeek = false
    var progress: Double = 0
    var currentTime: TimeInterval = 0
    var duration: TimeInterval = 0
    var playbackRate: Float = NarrationPlaybackService.defaultPlaybackRate

    @MainActor
    init(
        playbackService: NarrationPlaybackService,
        target: NarrationTarget?,
        isPreparing: Bool
    ) {
        let isCurrentTarget = target != nil && playbackService.speakingTarget == target
        let progressState = playbackService.progress
        self.isPlaying = isCurrentTarget && playbackService.isSpeaking
        self.isPreparing = isPreparing
        self.canSeek = isCurrentTarget && progressState.duration > 0
        self.currentTime = progressState.currentTime
        self.duration = progressState.duration
        self.progress = canSeek
            ? min(max(progressState.currentTime / progressState.duration, 0), 1)
            : 0
        self.playbackRate = playbackService.playbackRate
    }

    init(
        isPlaying: Bool = false,
        isPreparing: Bool = false,
        canSeek: Bool = false,
        currentTime: TimeInterval = 0,
        duration: TimeInterval = 0,
        playbackRate: Float = NarrationPlaybackService.defaultPlaybackRate
    ) {
        self.isPlaying = isPlaying
        self.isPreparing = isPreparing
        self.canSeek = canSeek
        self.currentTime = currentTime
        self.duration = duration
        self.progress = duration > 0 ? min(max(currentTime / duration, 0), 1) : 0
        self.playbackRate = playbackRate
    }

    var remainingTime: TimeInterval {
        canSeek ? max(duration - currentTime, 0) : 0
    }

    var currentTimeLabel: String { narrationTimeLabel(currentTime) }
    var durationLabel: String { narrationTimeLabel(duration) }

    /// "−3:12" while a duration is known; the scrubber shows elapsed on the
    /// other side so both ends of the track are labelled.
    var remainingTimeLabel: String {
        guard canSeek else { return narrationTimeLabel(0) }
        return "−" + narrationTimeLabel(remainingTime)
    }
}

func narrationTimeLabel(_ time: TimeInterval) -> String {
    guard time.isFinite, time > 0 else { return "0:00" }
    let totalSeconds = max(Int(time), 0)
    let minutes = totalSeconds / 60
    let seconds = totalSeconds % 60
    return "\(minutes):\(String(format: "%02d", seconds))"
}

/// Podcast playback in the detail header: play, scrubber, speed on one line.
struct NarrationPlaybackControlRow: View {
    private let playbackService: NarrationPlaybackService
    private let snapshot: NarrationPlaybackSnapshot

    let target: NarrationTarget?
    let onTogglePlayback: () -> Void

    init(
        playbackService: NarrationPlaybackService,
        target: NarrationTarget?,
        isPreparing: Bool,
        onTogglePlayback: @escaping () -> Void
    ) {
        self.playbackService = playbackService
        self.snapshot = NarrationPlaybackSnapshot(
            playbackService: playbackService,
            target: target,
            isPreparing: isPreparing
        )
        self.target = target
        self.onTogglePlayback = onTogglePlayback
    }

    var body: some View {
        HStack(spacing: 12) {
            NarrationPlayButton(
                snapshot: snapshot,
                diameter: 40,
                subject: "audio",
                action: onTogglePlayback
            )

            NarrationScrubber(
                snapshot: snapshot,
                onSeek: { nextProgress in
                    guard let target else { return }
                    playbackService.seek(to: nextProgress, for: target)
                }
            )

            NarrationSpeedMenu(playbackRate: snapshot.playbackRate) { rate in
                playbackService.setPlaybackRate(rate)
            }
        }
        .padding(.leading, 10)
        .padding(.trailing, 12)
        .padding(.vertical, 10)
        .background(Color.surfaceSecondary.opacity(0.85))
        .clipShape(RoundedRectangle(cornerRadius: CornerRadius.control, style: .continuous))
    }
}

/// Solid accent disc: the one saturated element on any playback surface.
struct NarrationPlayButton: View {
    let snapshot: NarrationPlaybackSnapshot
    var diameter: CGFloat = 44
    /// Noun for the accessibility label, e.g. "briefing audio".
    var subject = "audio"
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            ZStack {
                Circle()
                    .fill(Color.brandPrimary)

                if snapshot.isPreparing {
                    ProgressView()
                        .controlSize(diameter >= 40 ? .regular : .small)
                        .tint(Color.surfacePrimary)
                } else {
                    Image(systemName: snapshot.isPlaying ? "pause.fill" : "play.fill")
                        .font(.appSymbol(size: diameter * 0.38, weight: .bold))
                        .foregroundStyle(Color.surfacePrimary)
                        .offset(x: snapshot.isPlaying ? 0 : diameter * 0.04)
                        .contentTransition(.symbolEffect(.replace))
                }
            }
            .frame(width: diameter, height: diameter)
            .frame(minWidth: 44, minHeight: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(snapshot.isPreparing)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(accessibilityLabel)
        .accessibilityAddTraits(.isButton)
    }

    private var accessibilityLabel: String {
        if snapshot.isPreparing {
            return "Preparing \(subject)"
        }
        return snapshot.isPlaying ? "Pause \(subject)" : "Play \(subject)"
    }
}

/// One pill showing the current rate; the options live in a menu instead of
/// four side-by-side pills competing with the scrubber for width.
struct NarrationSpeedMenu: View {
    let playbackRate: Float
    let onSelect: (Float) -> Void

    var body: some View {
        Menu {
            ForEach(NarrationPlaybackSpeedOption.standardOptions) { option in
                Button {
                    onSelect(option.rate)
                } label: {
                    if isSelected(option) {
                        Label(option.title, systemImage: "checkmark")
                    } else {
                        Text(option.title)
                    }
                }
            }
        } label: {
            Text(NarrationPlaybackSpeedOption.title(for: playbackRate))
                .font(.appCaption.weight(.bold).monospacedDigit())
                .foregroundStyle(Color.onSurface)
                .lineLimit(1)
                .frame(minWidth: 44, minHeight: 32)
                .padding(.horizontal, 4)
                .background(
                    Capsule().stroke(Color.outlineVariant.opacity(0.9), lineWidth: 1)
                )
                .frame(minHeight: 44)
                .contentShape(Rectangle())
        }
        .menuOrder(.fixed)
        .buttonStyle(.plain)
        .accessibilityLabel("Playback speed")
        .accessibilityValue(NarrationPlaybackSpeedOption.title(for: playbackRate))
    }

    private func isSelected(_ option: NarrationPlaybackSpeedOption) -> Bool {
        abs(playbackRate - option.rate) < 0.001
    }
}

/// Draggable track with elapsed and remaining time at either end.
struct NarrationScrubber: View {
    let snapshot: NarrationPlaybackSnapshot
    let onSeek: (Double) -> Void

    @State private var dragProgress: Double?

    private var displayedProgress: Double {
        dragProgress ?? snapshot.progress
    }

    var body: some View {
        VStack(spacing: 4) {
            GeometryReader { geometry in
                let width = max(geometry.size.width, 1)
                let thumbX = width * displayedProgress
                let thumbDiameter: CGFloat = dragProgress == nil ? 12 : 16
                ZStack(alignment: .leading) {
                    Capsule()
                        .fill(Color.outlineVariant.opacity(0.7))
                        .frame(height: 4)

                    Capsule()
                        .fill(Color.brandPrimary)
                        .frame(width: max(thumbX, 0), height: 4)

                    Circle()
                        .fill(snapshot.canSeek ? Color.brandPrimary : Color.onSurfaceTertiary)
                        .frame(width: thumbDiameter, height: thumbDiameter)
                        .offset(x: thumbX - thumbDiameter / 2)
                }
                .frame(maxHeight: .infinity)
                .contentShape(Rectangle())
                .gesture(
                    DragGesture(minimumDistance: 0)
                        .onChanged { value in
                            guard snapshot.canSeek else { return }
                            let nextProgress = min(max(value.location.x / width, 0), 1)
                            dragProgress = nextProgress
                            onSeek(nextProgress)
                        }
                        .onEnded { _ in
                            dragProgress = nil
                        }
                )
            }
            .frame(height: 24)
            .animation(.easeOut(duration: 0.12), value: dragProgress == nil)

            HStack {
                Text(snapshot.currentTimeLabel)
                Spacer(minLength: 6)
                Text(snapshot.remainingTimeLabel)
            }
            .font(.appCaption2.weight(.medium).monospacedDigit())
            .foregroundStyle(Color.onSurfaceTertiary)
        }
        .opacity(snapshot.canSeek ? 1 : 0.6)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Playback progress")
        .accessibilityValue("\(snapshot.currentTimeLabel) of \(snapshot.durationLabel)")
        .accessibilityAdjustableAction { direction in
            guard snapshot.canSeek, snapshot.duration > 0 else { return }
            let step = 15 / snapshot.duration
            switch direction {
            case .increment: onSeek(min(snapshot.progress + step, 1))
            case .decrement: onSeek(max(snapshot.progress - step, 0))
            @unknown default: break
            }
        }
    }
}
