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
                        .stroke(Color.watercolorDiffusedPeach.opacity(0.45), lineWidth: 2.5)
                        .frame(width: 144, height: 144)
                        .scaleEffect(pulseScale)
                        .opacity(2.0 - Double(pulseScale))
                        .onAppear {
                            withAnimation(.easeInOut(duration: 1.2).repeatForever(autoreverses: true)) {
                                pulseScale = 1.15
                            }
                        }
                        .onDisappear { pulseScale = 1.0 }
                }

                Circle()
                    .fill(
                        LinearGradient(
                            colors: [Color.watercolorBase, Color.watercolorMistyBlue.opacity(0.42)],
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
                    .shadow(color: Color.watercolorSlate.opacity(0.14), radius: 12, x: 10, y: 10)
                    .shadow(color: .white.opacity(0.35), radius: 16, x: -8, y: -8)

                iconStack
            }
        }
        .buttonStyle(.plain)
        .disabled(audioState == .transcribing)
        .scaleEffect(isPressed ? 0.96 : 1.0)
        .animation(.easeInOut(duration: 0.15), value: isPressed)
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
                .font(.system(size: 36, weight: .medium))
                .foregroundColor(.watercolorSlate)
                .opacity(audioState == .idle || audioState == .error ? 1 : 0)
                .scaleEffect(audioState == .idle || audioState == .error ? 1 : 0.25)
                .blur(radius: audioState == .idle || audioState == .error ? 0 : 4)

            Image(systemName: "stop.fill")
                .font(.system(size: 30, weight: .medium))
                .foregroundColor(.watercolorDiffusedPeach)
                .opacity(audioState == .recording ? 1 : 0)
                .scaleEffect(audioState == .recording ? 1 : 0.25)
                .blur(radius: audioState == .recording ? 0 : 4)

            ProgressView()
                .tint(.watercolorSlate)
                .opacity(audioState == .transcribing ? 1 : 0)
                .scaleEffect(audioState == .transcribing ? 1 : 0.25)
                .blur(radius: audioState == .transcribing ? 0 : 4)
        }
        .animation(.spring(response: 0.3, dampingFraction: 1.0), value: audioState)
    }

    private var statusLabel: some View {
        VStack(spacing: 8) {
            if audioState == .recording {
                Text(formattedDuration)
                    .font(.title3.monospacedDigit())
                    .foregroundColor(.watercolorSlate.opacity(0.66))
            }

            Text(statusText)
                .font(.system(size: 11, weight: .medium))
                .tracking(2.5)
                .foregroundColor(.watercolorSlate.opacity(0.55))

            Text(statusDetail)
                .font(.caption)
                .foregroundColor(.watercolorSlate.opacity(0.68))
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
