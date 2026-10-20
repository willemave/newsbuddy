//
//  ChatStatusBanner.swift
//  newsly
//
//  Created by Assistant on 12/6/25.
//

import SwiftUI

/// A small banner that shows the status of an active chat session
struct ChatStatusBanner: View {
    let session: ActiveChatSession
    let onTap: () -> Void
    let onDismiss: () -> Void
    var style: BannerStyle = .floating

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var isPulsing = false

    enum BannerStyle {
        case floating  // Card with shadow (for overlays)
        case inline    // Simple bar (for inline content)
    }

    var body: some View {
        HStack(spacing: 12) {
            // Status indicator
            statusIndicator

            // Message
            VStack(alignment: .leading, spacing: 2) {
                Text(statusTitle)
                    .font(.appSubheadline)
                    .fontWeight(.medium)

                if style == .floating {
                    Text(session.contentTitle)
                        .font(.appCaption)
                        .foregroundColor(Color.onSurfaceSecondary)
                        .lineLimit(1)
                }
            }

            Spacer()

            // Action button or dismiss
            actionButton
        }
        .padding(.horizontal, style == .inline ? 20 : 16)
        .padding(.vertical, style == .inline ? 10 : 12)
        // The pulse dims the content, not the glass: opacity on glass flattens
        // it into a group that stops sampling what sits behind it.
        .opacity(!reduceMotion && isPulsing && style == .inline && isProcessing ? 0.7 : 1.0)
        .animation(
            reduceMotion ? nil : AppMotion.chatStatusPulse,
            value: isPulsing
        )
        .glassEffect(glass, in: bannerShape)
        // Glass is not hit-testable content; keep the whole banner tappable.
        .contentShape(bannerShape)
        .applyBannerStyle(style)
        .onAppear {
            if !reduceMotion && style == .inline && isProcessing {
                isPulsing = true
            }
        }
        .onChange(of: isProcessing) { _, processing in
            isPulsing = !reduceMotion && processing && style == .inline
        }
        .onChange(of: reduceMotion) { _, reduceMotion in
            isPulsing = !reduceMotion && isProcessing && style == .inline
        }
        .onTapGesture {
            if case .completed = session.status {
                onTap()
            }
        }
    }

    @ViewBuilder
    private var statusIndicator: some View {
        switch session.status {
        case .processing:
            ProgressView()
                .scaleEffect(0.8)
                .frame(width: 24, height: 24)

        case .completed:
            Image(systemName: "checkmark.circle.fill")
                .font(.appTitle3)
                .foregroundColor(Color.statusActive)

        case .failed:
            Image(systemName: "exclamationmark.circle.fill")
                .font(.appTitle3)
                .foregroundColor(Color.statusDestructive)
        }
    }

    private var statusTitle: String {
        switch session.status {
        case .processing:
            return "Preparing your deep dive..."
        case .completed:
            return "Analysis ready"
        case .failed(let error):
            return "Failed: \(error)"
        }
    }

    @ViewBuilder
    private var actionButton: some View {
        switch session.status {
        case .processing:
            // Show elapsed time or just a subtle indicator
            EmptyView()

        case .completed:
            Button(action: onTap) {
                Text("Open")
                    .font(.appSubheadline)
                    .fontWeight(.semibold)
            }
            .buttonStyle(.borderedProminent)
            .buttonBorderShape(.capsule)
            .controlSize(.small)

        case .failed:
            Button(action: onDismiss) {
                Image(systemName: "xmark")
                    .font(.appCaption)
                    .foregroundColor(Color.onSurfaceSecondary)
            }
        }
    }

    private var isProcessing: Bool {
        if case .processing = session.status { return true }
        return false
    }

    private var bannerShape: RoundedRectangle {
        RoundedRectangle(cornerRadius: style == .inline ? 14 : 16, style: .continuous)
    }

    private var glass: Glass {
        // Only a completed session opens on tap, so only it gets the press response.
        switch session.status {
        case .completed:
            return .floatingChrome.interactive()
        case .processing:
            return .floatingChrome
        case .failed:
            return .regular.tint(Color.statusDestructive.opacity(0.16))
        }
    }
}

// MARK: - Banner Style Modifier
private extension View {
    @ViewBuilder
    func applyBannerStyle(_ style: ChatStatusBanner.BannerStyle) -> some View {
        switch style {
        case .floating:
            self
                .padding(.horizontal, Spacing.appHorizontalMargin)
                .padding(.top, 8)
        case .inline:
            self
        }
    }
}

#Preview {
    VStack(spacing: 16) {
        ChatStatusBanner(
            session: ActiveChatSession(
                id: 1,
                contentId: 1,
                newsItemId: nil,
                contentTitle: "Understanding Modern AI Systems",
                messageId: 1,
                status: .processing
            ),
            onTap: {},
            onDismiss: {}
        )

        ChatStatusBanner(
            session: ActiveChatSession(
                id: 2,
                contentId: 2,
                newsItemId: nil,
                contentTitle: "The Future of Web Development",
                messageId: 2,
                status: .completed
            ),
            onTap: {},
            onDismiss: {}
        )

        ChatStatusBanner(
            session: ActiveChatSession(
                id: 3,
                contentId: 3,
                newsItemId: nil,
                contentTitle: "Some Article",
                messageId: 3,
                status: .failed("Network error")
            ),
            onTap: {},
            onDismiss: {}
        )
    }
    .padding()
    .background(Color.surfaceSecondary)
}
