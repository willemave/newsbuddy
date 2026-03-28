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
                    .font(.subheadline)
                    .fontWeight(.medium)

                if style == .floating {
                    Text(session.contentTitle)
                        .font(.caption)
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
        .background(backgroundColor)
        .applyBannerStyle(style)
        .opacity(isPulsing && style == .inline && isProcessing ? 0.7 : 1.0)
        .animation(.easeInOut(duration: 1.5).repeatForever(autoreverses: true), value: isPulsing)
        .onAppear {
            if style == .inline && isProcessing {
                isPulsing = true
            }
        }
        .onChange(of: isProcessing) { _, processing in
            isPulsing = processing && style == .inline
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
                .font(.title3)
                .foregroundColor(Color.statusActive)

        case .failed:
            Image(systemName: "exclamationmark.circle.fill")
                .font(.title3)
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
                    .font(.subheadline)
                    .fontWeight(.semibold)
            }
            .buttonStyle(.borderedProminent)
            .buttonBorderShape(.capsule)
            .controlSize(.small)

        case .failed:
            Button(action: onDismiss) {
                Image(systemName: "xmark")
                    .font(.caption)
                    .foregroundColor(Color.onSurfaceSecondary)
            }
        }
    }

    private var isProcessing: Bool {
        if case .processing = session.status { return true }
        return false
    }

    private var backgroundColor: Color {
        switch session.status {
        case .processing:
            return Color.surfaceSecondary
        case .completed:
            return Color.surfaceSecondary
        case .failed:
            return Color.statusDestructive.opacity(0.1)
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
                .cornerRadius(12)
                .shadow(color: .black.opacity(0.1), radius: 4, y: 2)
                .padding(.horizontal, 16)
                .padding(.top, 8)
        case .inline:
            self
                .cornerRadius(10)
                .overlay(
                    RoundedRectangle(cornerRadius: 10)
                        .stroke(Color.outlineVariant, lineWidth: 0.5)
                )
        }
    }
}

#Preview {
    VStack(spacing: 16) {
        ChatStatusBanner(
            session: ActiveChatSession(
                id: 1,
                contentId: 1,
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
