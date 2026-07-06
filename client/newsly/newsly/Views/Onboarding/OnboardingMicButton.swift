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
                        .stroke(Color.onboardingAmbientTertiary.opacity(0.45), lineWidth: 2.5)
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
                    .fill(
                        LinearGradient(
                            colors: [Color.onboardingSurface, Color.onboardingAmbientPrimary.opacity(0.42)],
                            startPoint: .topLeading,
                            endPoint: .bottomTrailing
                        )
                    )
                    .overlay(
                        Circle()
                            .stroke(Color.white.opacity(0.35), lineWidth: 1)
                    )
                    .overlay(alignment: .topLeading) {
                        Circle()
                            .fill(Color.white.opacity(0.45))
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
        .disabled(audioState == .transcribing)
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
                .opacity(audioState == .idle || audioState == .error ? 1 : 0)
                .scaleEffect(audioState == .idle || audioState == .error ? 1 : 0.25)
                .blur(radius: audioState == .idle || audioState == .error ? 0 : 4)

            Image(systemName: "stop.fill")
                .font(.appSymbol(size: 30, weight: .medium))
                .foregroundColor(.onboardingAmbientTertiary)
                .opacity(audioState == .recording ? 1 : 0)
                .scaleEffect(audioState == .recording ? 1 : 0.25)
                .blur(radius: audioState == .recording ? 0 : 4)

            ProgressView()
                .tint(.onboardingText)
                .opacity(audioState == .transcribing ? 1 : 0)
                .scaleEffect(audioState == .transcribing ? 1 : 0.25)
                .blur(radius: audioState == .transcribing ? 0 : 4)
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
                    .foregroundColor(.onboardingText.opacity(0.66))
            }

            Text(statusText)
                .font(.appSans(size: 11, weight: .medium))
                .tracking(2.5)
                .foregroundColor(.onboardingText.opacity(0.55))

            Text(statusDetail)
                .font(.appCaption)
                .foregroundColor(.onboardingText.opacity(0.68))
                .multilineTextAlignment(.center)
        }
    }

    private var statusText: String {
        switch audioState {
        case .idle: return "TAP TO SPEAK"
        case .recording: return "LISTENING"
        case .transcribing: return "PROCESSING"
        case .error: return "TAP TO RETRY"
        }
    }

    private var statusDetail: String {
        switch audioState {
        case .idle:
            return "Say a few topics, names, or newsletters."
        case .recording:
            return "Tap again when you're done."
        case .transcribing:
            return "Matching newsletters, podcasts, and Reddit."
        case .error:
            return "We missed that. Give it another try."
        }
    }

    private var accessibilityText: String {
        switch audioState {
        case .idle: return "Tap to start recording"
        case .recording: return "Recording. Tap to stop."
        case .transcribing: return "Processing speech"
        case .error: return "Tap to retry recording"
        }
    }

    private var formattedDuration: String {
        let minutes = durationSeconds / 60
        let seconds = durationSeconds % 60
        return String(format: "%d:%02d", minutes, seconds)
    }

    private func handleTap() {
        switch audioState {
        case .idle, .error:
            onStart()
        case .recording:
            onStop()
        case .transcribing:
            break
        }
    }
}
