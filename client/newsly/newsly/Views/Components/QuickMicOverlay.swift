import SwiftUI

struct QuickMicOverlay: View {
    @ObservedObject var viewModel: QuickMicViewModel
    let screenContext: AssistantScreenContext
    let isVisible: Bool
    var showsIdleMic: Bool = true
    let onOpenChatSession: (Int) -> Void

    @Namespace private var micNamespace
    private let bottomBarClearance: CGFloat = 104

    var body: some View {
        ZStack(alignment: .bottom) {
            if viewModel.isModalPresented {
                VStack(spacing: 0) {
                    Color.black.opacity(0.06)
                        .contentShape(Rectangle())
                        .onTapGesture {
                            // Keep the quick session alive until the user explicitly closes it.
                        }

                    Color.clear
                        .frame(height: bottomBarClearance)
                }
                .ignoresSafeArea()
                .transition(.opacity)

                panel
                    .padding(.horizontal, 14)
                    .padding(.bottom, bottomBarClearance)
                    .transition(.move(edge: .bottom).combined(with: .opacity))
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottom)
            }

            if showsIdleMic && isVisible && !viewModel.isModalPresented {
                floatingMic
                    .padding(.leading, 20)
                    .padding(.bottom, 42)
                    .transition(.scale.combined(with: .opacity))
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottomLeading)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .animation(.spring(response: 0.32, dampingFraction: 0.82), value: viewModel.isModalPresented)
        .animation(.spring(response: 0.32, dampingFraction: 0.82), value: viewModel.isRecording)
    }

    private var panelStatusText: String {
        switch viewModel.state {
        case .idle:
            return "Ready when you are"
        case .recordingWaveform:
            return "Listening"
        case .finalizingTranscript:
            return "Finalizing your question"
        case .submittingTurn:
            return "Thinking through it"
        case .modalActive:
            return "Hold the bottom mic to ask again"
        case .failed:
            return "Try that one more time"
        }
    }

    private var statusAccentColor: Color {
        switch viewModel.state {
        case .failed:
            return .red
        case .recordingWaveform:
            return .accentColor
        case .finalizingTranscript, .submittingTurn:
            return .orange
        case .idle, .modalActive:
            return .secondary
        }
    }

    private var floatingMic: some View {
        HoldToTalkMicButton(
            isEnabled: viewModel.isAvailable,
            isRecording: viewModel.isRecording,
            size: 74,
            namespace: micNamespace,
            matchedId: "quick-mic",
            onPressStart: {
                Task { await viewModel.beginHold(screenContext: screenContext) }
            },
            onPressEnd: {
                Task { await viewModel.endHold() }
            }
        )
        .shadow(color: .black.opacity(0.18), radius: 16, y: 10)
        .overlay(
            Circle()
                .stroke(Color.white.opacity(0.85), lineWidth: 3)
        )
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Quick Assistant")
        .accessibilityHint("Press and hold to record a quick assistant turn")
        .accessibilityIdentifier("quick_mic.tabbar")
    }

    private var panel: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .top, spacing: 12) {
                VStack(alignment: .leading, spacing: 6) {
                    HStack(spacing: 8) {
                        Circle()
                            .fill(statusAccentColor)
                            .frame(width: 8, height: 8)

                        Text("Quick Assistant")
                            .font(.system(size: 18, weight: .semibold, design: .rounded))
                    }

                    Text(panelStatusText)
                        .font(.system(size: 13, weight: .medium, design: .rounded))
                        .foregroundStyle(statusAccentColor)

                    if viewModel.activeSession != nil {
                        Text("Tap the close button to end this quick session.")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                }

                Spacer(minLength: 8)

                if viewModel.isRecording {
                    Text("Live")
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(Color.accentColor)
                        .padding(.horizontal, 9)
                        .padding(.vertical, 5)
                        .background(Color.accentColor.opacity(0.12))
                        .clipShape(Capsule())
                }

                Button {
                    viewModel.dismissPanel()
                } label: {
                    Image(systemName: "xmark")
                        .font(.system(size: 13, weight: .bold))
                        .foregroundStyle(.secondary)
                        .frame(width: 30, height: 30)
                        .background(Color.black.opacity(0.05))
                        .clipShape(Circle())
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("quick_mic.close")
                .accessibilityHint("Close quick assistant and clear this quick session")
            }

            if let errorMessage = viewModel.errorMessage {
                Text(errorMessage)
                    .font(.system(size: 14, weight: .medium, design: .rounded))
                    .foregroundStyle(.red)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 10)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Color.red.opacity(0.08))
                    .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
            }

            ScrollView(showsIndicators: false) {
                VStack(alignment: .leading, spacing: 10) {
                    ForEach(viewModel.messages) { message in
                        QuickMicMessageBubble(message: message)
                    }

                    if viewModel.state == .recordingWaveform {
                        QuickMicStatusBubble(
                            title: "Listening",
                            text: viewModel.activeTranscript.isEmpty
                                ? "Keep holding the bottom mic and speak naturally."
                                : viewModel.activeTranscript,
                            systemImage: "waveform",
                            accentColor: .accentColor
                        )
                    }

                    if viewModel.state == .finalizingTranscript || viewModel.state == .submittingTurn {
                        QuickMicStatusBubble(
                            title: "Newsly",
                            text: viewModel.state == .finalizingTranscript
                                ? "Transcribing your message..."
                                : "Thinking through a concise answer...",
                            systemImage: "sparkles",
                            accentColor: .orange,
                            showsProgress: true
                        )
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(maxHeight: viewModel.messages.isEmpty ? 92 : 240)

            HStack(spacing: 10) {
                if let sessionId = viewModel.activeSession?.id {
                    Button {
                        onOpenChatSession(sessionId)
                        viewModel.dismissPanel()
                    } label: {
                        Label("Open full chat", systemImage: "arrow.up.left.and.arrow.down.right")
                            .font(.system(size: 13, weight: .semibold, design: .rounded))
                            .foregroundStyle(.primary)
                            .padding(.horizontal, 12)
                            .padding(.vertical, 9)
                            .background(Color.black.opacity(0.05))
                            .clipShape(Capsule())
                    }
                    .buttonStyle(.plain)
                }

                Spacer()

                Text(viewModel.activeSession == nil ? "Press and hold to ask." : "Use the bottom mic to keep the same session going.")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(18)
        .background(
            RoundedRectangle(cornerRadius: 26, style: .continuous)
                .fill(.regularMaterial)
                .overlay {
                    RoundedRectangle(cornerRadius: 26, style: .continuous)
                        .fill(
                            LinearGradient(
                                colors: [
                                    Color.white.opacity(0.58),
                                    Color.white.opacity(0.18),
                                ],
                                startPoint: .topLeading,
                                endPoint: .bottomTrailing
                            )
                        )
                }
        )
        .overlay(
            RoundedRectangle(cornerRadius: 26, style: .continuous)
                .stroke(Color.white.opacity(0.55), lineWidth: 1)
        )
        .shadow(color: .black.opacity(0.12), radius: 22, y: 12)
        .frame(maxWidth: 390)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("quick_mic.panel")
    }
}

private struct QuickMicMessageBubble: View {
    let message: ChatMessage
    @Environment(\.openURL) private var openURL
    @StateObject private var feedOptionActionModel = AssistantFeedOptionActionModel()

    private var isUser: Bool {
        message.role == .user
    }

    var body: some View {
        HStack {
            if isUser {
                Spacer(minLength: 42)
            }

            VStack(alignment: .leading, spacing: 5) {
                Text(isUser ? "You" : "Newsly")
                    .font(.system(size: 11, weight: .semibold, design: .rounded))
                    .foregroundStyle(isUser ? Color.accentColor.opacity(0.88) : .secondary)

                VStack(alignment: .leading, spacing: 10) {
                    Text(message.content)
                        .font(.system(size: 15, weight: .regular, design: .rounded))
                        .foregroundStyle(.primary)
                        .lineSpacing(2)
                        .multilineTextAlignment(.leading)

                    if !isUser && message.hasFeedOptions {
                        AssistantFeedOptionsSection(
                            options: message.feedOptions,
                            actionModel: feedOptionActionModel,
                            onPreview: { option in
                                guard let url = URL(string: option.previewURLString) else { return }
                                openURL(url)
                            }
                        )
                    }
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 10)
            .background(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .fill(isUser ? Color.accentColor.opacity(0.12) : Color.white.opacity(0.8))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .stroke(
                        isUser ? Color.accentColor.opacity(0.18) : Color.black.opacity(0.05),
                        lineWidth: 1
                    )
            )
            .shadow(color: .black.opacity(0.05), radius: 10, y: 4)
            .frame(maxWidth: 292, alignment: isUser ? .trailing : .leading)
            .frame(maxWidth: .infinity, alignment: isUser ? .trailing : .leading)

            if !isUser {
                Spacer(minLength: 42)
            }
        }
    }
}

private struct QuickMicStatusBubble: View {
    let title: String
    let text: String
    let systemImage: String
    var accentColor: Color = .secondary
    var showsProgress: Bool = false

    var body: some View {
        HStack {
            Spacer(minLength: 42)

            VStack(alignment: .leading, spacing: 8) {
                Text(title)
                    .font(.system(size: 11, weight: .semibold, design: .rounded))
                    .foregroundStyle(.secondary)

                HStack(alignment: .top, spacing: 8) {
                    if showsProgress {
                        ProgressView()
                            .progressViewStyle(.circular)
                            .tint(accentColor)
                    } else {
                        Image(systemName: systemImage)
                            .font(.system(size: 12, weight: .semibold))
                            .foregroundStyle(accentColor)
                            .frame(width: 16, height: 16)
                    }

                    Text(text)
                        .font(.system(size: 15, weight: .regular, design: .rounded))
                        .foregroundStyle(.primary)
                        .lineSpacing(2)
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 10)
            .background(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .fill(Color.white.opacity(0.72))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .stroke(Color.black.opacity(0.05), lineWidth: 1)
            )
            .frame(maxWidth: 292, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

struct HoldToTalkMicButton: View {
    let isEnabled: Bool
    let isRecording: Bool
    let size: CGFloat
    let namespace: Namespace.ID
    let matchedId: String
    var tint: Color = .accentColor
    let onPressStart: () -> Void
    let onPressEnd: () -> Void

    @State private var didStartPress = false

    var body: some View {
        ZStack {
            Circle()
                .fill(
                    LinearGradient(
                        colors: isEnabled
                            ? [tint, tint.opacity(0.82)]
                            : [Color.gray.opacity(0.5), Color.gray.opacity(0.42)],
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    )
                )
                .matchedGeometryEffect(id: matchedId, in: namespace)

            if isRecording {
                WaveformGlyph()
                    .frame(width: size * 0.46, height: size * 0.26)
            } else {
                Image(systemName: "mic.fill")
                    .font(.system(size: size * 0.34, weight: .semibold))
                    .foregroundStyle(.white)
            }
        }
        .frame(width: size, height: size)
        .scaleEffect(isRecording ? 1.08 : 1.0)
        .opacity(isEnabled ? 1.0 : 0.72)
        .gesture(
            DragGesture(minimumDistance: 0)
                .onChanged { _ in
                    guard isEnabled else { return }
                    guard !didStartPress else { return }
                    didStartPress = true
                    onPressStart()
                }
                .onEnded { _ in
                    guard didStartPress else { return }
                    didStartPress = false
                    onPressEnd()
                }
        )
    }
}

struct TapToTalkMicButton: View {
    let isEnabled: Bool
    let isRecording: Bool
    let isBusy: Bool
    let size: CGFloat
    var tint: Color = .accentColor
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            ZStack {
                Circle()
                    .fill(
                        LinearGradient(
                            colors: isEnabled || isRecording
                                ? [tint, tint.opacity(0.82)]
                                : [Color.gray.opacity(0.5), Color.gray.opacity(0.42)],
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
                        .tint(.white)
                        .controlSize(.small)
                } else if isRecording {
                    Image(systemName: "stop.fill")
                        .font(.system(size: size * 0.3, weight: .bold))
                        .foregroundStyle(.white)
                } else {
                    Image(systemName: "mic.fill")
                        .font(.system(size: size * 0.34, weight: .semibold))
                        .foregroundStyle(.white)
                }
            }
            .frame(width: size, height: size)
            .scaleEffect(isRecording ? 1.05 : 1.0)
            .shadow(color: tint.opacity(isRecording ? 0.22 : 0.12), radius: isRecording ? 12 : 8, y: 6)
            .animation(.easeInOut(duration: 0.18), value: isRecording)
            .animation(.easeInOut(duration: 0.18), value: isBusy)
        }
        .buttonStyle(.plain)
        .disabled(!isEnabled)
    }
}

struct WaveformGlyph: View {
    var body: some View {
        TimelineView(.animation) { context in
            let time = context.date.timeIntervalSinceReferenceDate
            HStack(spacing: 4) {
                ForEach(0..<5, id: \.self) { index in
                    let amplitude = 0.32 + abs(sin(time * 3.6 + Double(index) * 0.45)) * 0.68
                    Capsule()
                        .fill(Color.white)
                        .frame(width: 4, height: 12 + amplitude * 22)
                }
            }
            .animation(.easeInOut(duration: 0.16), value: time)
        }
    }
}
