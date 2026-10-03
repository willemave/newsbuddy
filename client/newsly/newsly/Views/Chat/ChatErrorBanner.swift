//
//  ChatErrorBanner.swift
//  newsly
//

import SwiftUI

struct ChatErrorBanner: View {
    let error: String
    let onDismiss: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.appSymbol(size: 15, weight: .semibold))
                .foregroundStyle(Color.statusDestructive)

            Text(error)
                .font(.terracottaBodySmall)
                .foregroundStyle(Color.onSurface)
            .frame(maxWidth: .infinity, alignment: .leading)

            Button(action: onDismiss) {
                Image(systemName: "xmark")
                    .font(.appSymbol(size: 12, weight: .semibold))
                    .foregroundStyle(Color.onSurfaceSecondary)
                    .frame(width: 24, height: 24)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Dismiss error")
            .accessibilityIdentifier("knowledge.chat_error_dismiss")
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 12)
        .background(Color.statusDestructive.opacity(0.12))
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .stroke(Color.statusDestructive.opacity(0.22), lineWidth: 1)
        )
        .padding(.horizontal, 16)
        .accessibilityIdentifier("knowledge.chat_error_banner")
    }
}

#if DEBUG
#Preview("Chat Error Banner") {
    ChatErrorBanner(
        error: "The message could not be sent.",
        onDismiss: {}
    )
    .padding(.vertical)
    .background(Color.surfacePrimary)
}
#endif
