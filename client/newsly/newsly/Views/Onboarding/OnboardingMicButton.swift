//
//  OnboardingMicButton.swift
//  newsly
//

import SwiftUI

struct OnboardingMicButton: View {
    let audioState: OnboardingAudioState
    let durationSeconds: Int
    let onStart: () -> Void
    let onStop: () -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var isPressed = false
    @State private var pulseScale: CGFloat = 1.0

    var body: some View {
        VStack(spacing: 28) {
            micButton
            statusLabel
        }
    }

    private var micButton: some View {
        Button(action: handleTap) {
            ZStack {
                if audioState == .recording {
                    Circle()
                        .stroke(Color.onboardingSelectionAccent.opacity(0.45), lineWidth: 2.5)
                        .frame(width: 144, height: 144)
                        .scaleEffect(reduceMotion ? 1.0 : pulseScale)
                        .opacity(reduceMotion ? 0.78 : 2.0 - Double(pulseScale))
                        .onAppear {
                            guard !reduceMotion else { return }
                            withAnimation(AppMotion.recordingPulse) {
                                pulseScale = 1.15
                            }
                        }
                        .onDisappear { pulseScale = 1.0 }
                        .onChange(of: reduceMotion) { _, reduceMotion in
                            if reduceMotion {
                                pulseScale = 1.0
                            } else {
                                withAnimation(AppMotion.recordingPulse) {
                                    pulseScale = 1.15
                                }
                            }
                        }
                }

                Circle()
                    .fill(Color.surfaceSecondary)
                    .overlay(
                        Circle()
                            .stroke(sheenColor(0.35), lineWidth: 1)
                    )
                    .overlay(alignment: .topLeading) {
                        Circle()
                            .fill(sheenColor(0.45))
                            .frame(width: 42, height: 42)
                            .blur(radius: 18)
                            .offset(x: 18, y: 18)
                    }
                    .frame(width: 128, height: 128)
                    .appShadow(.onboardingMic)

                iconStack
            }
        }
        .buttonStyle(.plain)
        .disabled(audioState == .starting || audioState == .transcribing)
        .scaleEffect(isPressed ? 0.96 : 1.0)
        .animation(AppMotion.press, value: isPressed)
        .simultaneousGesture(
            DragGesture(minimumDistance: 0)
                .onChanged { _ in isPressed = true }
                .onEnded { _ in isPressed = false }
        )
        .accessibilityIdentifier("onboarding.audio.mic")
        .accessibilityLabel(accessibilityText)
    }

    private var iconStack: some View {
        ZStack {
            Image(systemName: "mic.fill")
                .font(.appSymbol(size: 36, weight: .medium))
                .foregroundColor(.onboardingText)
                .opacity(audioState == .idle || audioState == .failed ? 1 : 0)
                .scaleEffect(audioState == .idle || audioState == .failed ? 1 : 0.25)
                .blur(radius: audioState == .idle || audioState == .failed ? 0 : 4)

            Image(systemName: "stop.fill")
                .font(.appSymbol(size: 30, weight: .medium))
                .foregroundColor(.onboardingSelectionAccent)
                .opacity(audioState == .recording ? 1 : 0)
                .scaleEffect(audioState == .recording ? 1 : 0.25)
                .blur(radius: audioState == .recording ? 0 : 4)

            ProgressView()
                .tint(.onboardingText)
                .opacity(audioState == .starting || audioState == .transcribing ? 1 : 0)
                .scaleEffect(audioState == .starting || audioState == .transcribing ? 1 : 0.25)
                .blur(radius: audioState == .starting || audioState == .transcribing ? 0 : 4)
        }
        .animation(AppMotion.panel, value: audioState)
    }

    private var statusLabel: some View {
        VStack(spacing: 8) {
            if audioState == .recording {
                Text(formattedDuration)
                    .font(
                        .appSans(size: 20, relativeTo: .title3, weight: .semibold)
                            .monospacedDigit()
                    )
                    .foregroundColor(.onSurfaceSecondary)
            }

            Text(statusText)
                .font(.appSans(size: 11, weight: .medium))
                .tracking(2.5)
                .foregroundColor(.onSurfaceTertiary)
                .accessibilityIdentifier(
                    "onboarding.audio.state.\(audioState.accessibilityIdentifier)"
                )

            if let statusDetail {
                Text(statusDetail)
                    .font(.appCaption)
                    .foregroundColor(.onSurfaceSecondary)
                    .multilineTextAlignment(.center)
            }
        }
    }

    private var statusText: String {
        switch audioState {
        case .idle: return "TAP TO SPEAK"
        case .starting: return "STARTING"
        case .recording: return "LISTENING"
        case .transcribing: return "PROCESSING"
        case .failed: return "TAP TO RETRY"
        }
    }

    private var statusDetail: String? {
        switch audioState {
        case .idle:
            return "Say a few topics, names, or newsletters."
        case .starting:
            return "Getting the microphone ready."
        case .recording:
            return nil
        case .transcribing:
            return "Matching newsletters, podcasts, and Reddit."
        case .failed:
            return "We missed that. Give it another try."
        }
    }

    /// White specular sheen in light mode; nearly gone on dark charcoal where a
    /// bright rim would read as a hard edge instead of a highlight.
    private func sheenColor(_ lightOpacity: CGFloat) -> Color {
        Color(UIColor { tc in
            UIColor.white.withAlphaComponent(
                tc.userInterfaceStyle == .dark ? lightOpacity * 0.2 : lightOpacity
            )
        })
    }

    private var accessibilityText: String {
        switch audioState {
        case .idle: return "Tap to start recording"
        case .starting: return "Starting microphone"
        case .recording: return "Recording. Tap to stop."
        case .transcribing: return "Processing speech"
        case .failed: return "Tap to retry recording"
        }
    }

    private var formattedDuration: String {
        let minutes = durationSeconds / 60
        let seconds = durationSeconds % 60
        return String(format: "%d:%02d", minutes, seconds)
    }

    private func handleTap() {
        switch audioState {
        case .idle, .failed:
            onStart()
        case .recording:
            onStop()
        case .starting, .transcribing:
            break
        }
    }
}
