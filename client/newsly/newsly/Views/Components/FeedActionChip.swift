//
//  FeedActionChip.swift
//  newsly
//
//  Shared quick-action chip for the Fast Read and Long Read feed headers.
//

import SwiftUI

struct FeedActionChip: View {
    let title: String
    let systemImage: String
    var isLoading = false

    var body: some View {
        HStack(spacing: 8) {
            ZStack {
                Image(systemName: systemImage)
                    .font(.appSymbol(size: 13, weight: .semibold))
                    .foregroundStyle(Color.brandPrimary)
                    .contentTransition(.symbolEffect(.replace))
                    .animation(AppMotion.subtle, value: systemImage)
                    .opacity(isLoading ? 0 : 1)

                ProgressView()
                    .controlSize(.small)
                    .tint(Color.brandPrimary)
                    .opacity(isLoading ? 1 : 0)
            }
            .animation(AppMotion.subtle, value: isLoading)

            Text(title)
                .font(.terracottaBodyMedium.weight(.semibold))
                .foregroundStyle(Color.onSurface)
                .lineLimit(1)
        }
        .padding(.horizontal, 16)
        .frame(minHeight: 44)
        .background(Color.surfaceSecondary)
        .clipShape(Capsule())
        .overlay {
            Capsule()
                .stroke(Color.outlineVariant.opacity(0.3), lineWidth: 1)
        }
    }
}

#Preview {
    HStack(spacing: 10) {
        FeedActionChip(title: "Audio Brief", systemImage: "waveform")
        FeedActionChip(title: "Best Unread", systemImage: "sparkles", isLoading: true)
    }
    .padding()
    .background(Color.surfacePrimary)
}
