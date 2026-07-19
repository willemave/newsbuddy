//
//  ChatActivityViews.swift
//  newsly
//

import SwiftUI

struct ThinkingBubbleView: View {
    let startDate: Date?
    let statusText: String?

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var isAnimating = false

    private func elapsedSeconds(at date: Date) -> Int {
        guard let startDate else { return 0 }
        return max(Int(date.timeIntervalSince(startDate)), 0)
    }

    private func formattedDuration(elapsedSeconds: Int) -> String {
        String(format: "%02d:%02d", elapsedSeconds / 60, elapsedSeconds % 60)
    }

    var body: some View {
        TimelineView(.periodic(from: startDate ?? Date(), by: 1.0)) { timeline in
            let elapsedSeconds = elapsedSeconds(at: timeline.date)
            content(elapsedSeconds: elapsedSeconds)
        }
    }

    private func content(elapsedSeconds: Int) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                HStack(spacing: 6) {
                    ForEach(0..<3) { index in
                        Circle()
                            .fill(Color.chatAccent.opacity(0.5))
                            .frame(width: 6, height: 6)
                            .offset(y: reduceMotion ? 0 : (isAnimating ? -2 : 2))
                            .animation(
                                reduceMotion ? nil : AppMotion.typingDotPulse
                                    .delay(Double(index) * 0.1),
                                value: isAnimating
                            )
                    }
                }

                Text(formattedDuration(elapsedSeconds: elapsedSeconds))
                    .font(.appCaption2)
                    .foregroundStyle(Color.onSurfaceSecondary)
                    .monospacedDigit()
            }

            if let statusText, !statusText.isEmpty {
                Text(statusText)
                    .font(.appCaption)
                    .foregroundStyle(Color.onSurfaceSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 12)
        .background(Color.surfaceContainer)
        .clipShape(UnevenRoundedRectangle(topLeadingRadius: 4, bottomLeadingRadius: 16, bottomTrailingRadius: 16, topTrailingRadius: 16))
        .frame(maxWidth: .infinity, alignment: .leading)
        .onAppear {
            guard !reduceMotion else { return }
            isAnimating = true
        }
        .onChange(of: reduceMotion) { _, reduceMotion in
            isAnimating = !reduceMotion
        }
    }
}

struct InitialSuggestionsLoadingView: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var dotOffset: CGFloat = 0
    @State private var pulseScale: CGFloat = 1.0

    var body: some View {
        VStack(spacing: 20) {
            ZStack {
                Circle()
                    .fill(Color.chatAccent.opacity(0.08))
                    .frame(width: 80, height: 80)
                    .scaleEffect(reduceMotion ? 1.0 : pulseScale)

                HStack(spacing: 6) {
                    ForEach(0..<3) { index in
                        Circle()
                            .fill(Color.chatAccent.opacity(0.7))
                            .frame(width: 10, height: 10)
                            .offset(y: reduceMotion ? 0 : dotOffset)
                            .animation(
                                reduceMotion ? nil : AppMotion.typingDotPulse
                                    .delay(Double(index) * 0.12),
                                value: dotOffset
                            )
                    }
                }
            }
            .onAppear {
                guard !reduceMotion else { return }
                dotOffset = -6
                withAnimation(AppMotion.chatStatusPulse) {
                    pulseScale = 1.15
                }
            }
            .onChange(of: reduceMotion) { _, reduceMotion in
                if reduceMotion {
                    dotOffset = 0
                    pulseScale = 1.0
                } else {
                    dotOffset = -6
                    withAnimation(AppMotion.chatStatusPulse) {
                        pulseScale = 1.15
                    }
                }
            }

            VStack(spacing: 6) {
                Text("Preparing suggestions")
                    .font(.appHeadline)
                    .foregroundStyle(Color.onSurface)

                Text("Analyzing the article for you")
                    .font(.appSubheadline)
                    .foregroundStyle(Color.onSurfaceSecondary)
            }
        }
    }
}

#Preview("Loading State") {
    InitialSuggestionsLoadingView()
}
