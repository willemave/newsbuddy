import SwiftUI

struct TapToTalkMicButton: View {
    let isEnabled: Bool
    let isRecording: Bool
    let isTranscribing: Bool
    let isBusy: Bool
    let size: CGFloat
    var tint: Color = .brandPrimary
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            ZStack {
                Circle()
                    .fill(
                        LinearGradient(
                            colors: isEnabled || isRecording
                                ? [tint, tint.opacity(0.82)]
                                : [
                                    Color.surfaceContainerHighest.opacity(0.9),
                                    Color.surfaceContainerHighest.opacity(0.75),
                                ],
                            startPoint: .topLeading,
                            endPoint: .bottomTrailing
                        )
                    )

                Circle()
                    .stroke(Color.white.opacity(isRecording ? 0.26 : 0.14), lineWidth: 1)

                Circle()
                    .stroke(tint.opacity(isRecording ? 0.26 : 0), lineWidth: 8)
                    .scaleEffect(isRecording ? 1.14 : 0.92)

                if isBusy {
                    ProgressView()
                        .tint(Color.surfacePrimary)
                        .controlSize(.small)
                } else if isRecording {
                    Image(systemName: "stop.fill")
                        .font(.appSymbol(size: size * 0.3, weight: .bold))
                        .foregroundStyle(Color.surfacePrimary)
                } else {
                    Image(systemName: "mic.fill")
                        .font(.appSymbol(size: size * 0.34, weight: .semibold))
                        .foregroundStyle(Color.surfacePrimary)
                }
            }
            .frame(width: size, height: size)
            .scaleEffect(isRecording ? 1.05 : 1.0)
            .appShadow(.voiceControl(tint: tint, isActive: isRecording))
            .animation(AppMotion.subtle, value: isRecording)
            .animation(AppMotion.subtle, value: isBusy)
        }
        .buttonStyle(.plain)
        .disabled(!isEnabled)
        .sensoryFeedback(.impact(weight: .light), trigger: isRecording) { _, isRecording in
            isRecording
        }
        .sensoryFeedback(.success, trigger: isTranscribing) { _, isTranscribing in
            isTranscribing
        }
    }
}
