//
//  ChatActivityViews.swift
//  newsly
//

import SwiftUI

struct ThinkingBubbleView: View {
    let elapsedSeconds: Int
    let statusText: String?
    @State private var isAnimating = false

    private var formattedDuration: String {
        String(format: "%02d:%02d", elapsedSeconds / 60, elapsedSeconds % 60)
    }

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Circle()
                .fill(Color.chatAccent)
                .frame(width: 24, height: 24)
                .overlay(
                    Image(systemName: "brain.head.profile")
                        .font(.system(size: 11))
                        .foregroundStyle(.white)
                )
                .padding(.top, 2)

            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 6) {
                    ForEach(0..<3) { index in
                        Circle()
                            .fill(Color.chatAccent.opacity(0.5))
                            .frame(width: 6, height: 6)
                            .offset(y: isAnimating ? -2 : 2)
                            .animation(
                                .easeInOut(duration: 0.4)
                                    .repeatForever(autoreverses: true)
                                    .delay(Double(index) * 0.1),
                                value: isAnimating
                            )
                    }
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 12)
                .background(Color.surfaceContainer)
                .clipShape(UnevenRoundedRectangle(topLeadingRadius: 4, bottomLeadingRadius: 16, bottomTrailingRadius: 16, topTrailingRadius: 16))

                if let statusText, !statusText.isEmpty {
                    Text(statusText)
                        .font(.caption)
                        .foregroundStyle(Color.onSurfaceSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.horizontal, 4)
                }

                Text(formattedDuration)
                    .font(.caption2)
                    .foregroundStyle(Color.onSurfaceSecondary)
                    .monospacedDigit()
                    .padding(.horizontal, 4)
            }
        }
        .onAppear {
            isAnimating = true
        }
    }
}

struct InitialSuggestionsLoadingView: View {
    @State private var dotOffset: CGFloat = 0
    @State private var pulseScale: CGFloat = 1.0

    var body: some View {
        VStack(spacing: 20) {
            ZStack {
                Circle()
                    .fill(Color.chatAccent.opacity(0.08))
                    .frame(width: 80, height: 80)
                    .scaleEffect(pulseScale)

                HStack(spacing: 6) {
                    ForEach(0..<3) { index in
                        Circle()
                            .fill(Color.chatAccent.opacity(0.7))
                            .frame(width: 10, height: 10)
                            .offset(y: dotOffset)
                            .animation(
                                .easeInOut(duration: 0.4)
                                    .repeatForever(autoreverses: true)
                                    .delay(Double(index) * 0.12),
                                value: dotOffset
                            )
                    }
                }
            }
            .onAppear {
                dotOffset = -6
                withAnimation(.easeInOut(duration: 1.5).repeatForever(autoreverses: true)) {
                    pulseScale = 1.15
                }
            }

            VStack(spacing: 6) {
                Text("Preparing suggestions")
                    .font(.headline)
                    .foregroundStyle(Color.onSurface)

                Text("Analyzing the article for you")
                    .font(.subheadline)
                    .foregroundStyle(Color.onSurfaceSecondary)
            }
        }
    }
}

#Preview("Loading State") {
    InitialSuggestionsLoadingView()
}
