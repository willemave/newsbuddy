//
//  ChatErrorBanner.swift
//  newsly
//

import SwiftUI

struct ChatErrorBanner: View {
    let error: String
    let onAddExperts: () -> Void
    let onDismiss: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(Color.statusDestructive)

            VStack(alignment: .leading, spacing: 6) {
                if isCouncilConfigurationError {
                    Text("Council setup required")
                        .font(.terracottaBodySmall.weight(.semibold))
                        .foregroundStyle(Color.onSurface)

                    Text("Add at least two experts in Settings to enable council chat.")
                        .font(.terracottaBodySmall)
                        .foregroundStyle(Color.onSurface)

                    Button("Add Experts", action: onAddExperts)
                        .buttonStyle(.plain)
                        .font(.terracottaBodySmall.weight(.semibold))
                        .foregroundStyle(Color.terracottaPrimary)
                        .accessibilityIdentifier("knowledge.chat_error_add_experts")
                } else {
                    Text(error)
                        .font(.terracottaBodySmall)
                        .foregroundStyle(Color.onSurface)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            Button(action: onDismiss) {
                Image(systemName: "xmark")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(Color.onSurfaceSecondary)
                    .frame(width: 24, height: 24)
            }
            .buttonStyle(.plain)
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

    private var isCouncilConfigurationError: Bool {
        let normalized = error.lowercased()
        return normalized.contains("add at least")
            && normalized.contains("experts")
            && normalized.contains("council")
    }
}

#if DEBUG
#Preview("Chat Error Banner") {
    ChatErrorBanner(
        error: "Add at least two experts in Settings to enable council chat.",
        onAddExperts: {},
        onDismiss: {}
    )
    .padding(.vertical)
    .background(Color.surfacePrimary)
}
#endif
