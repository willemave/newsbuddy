//
//  ChatLoadingView.swift
//  newsly
//
//  Created by Assistant on 11/28/25.
//

import SwiftUI

struct ChatLoadingView: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var scale: CGFloat = 1.0
    @State private var bubbleOffset: CGFloat = 0

    var body: some View {
        VStack(spacing: 20) {
            // Animated chat bubbles
            ZStack {
                // Background glow
                Circle()
                    .fill(
                        RadialGradient(
                            colors: [Color.brandPrimary.opacity(0.15), Color.clear],
                            center: .center,
                            startRadius: 20,
                            endRadius: 60
                        )
                    )
                    .frame(width: 120, height: 120)
                    .scaleEffect(reduceMotion ? 1.0 : scale)

                // Three floating chat bubbles
                HStack(spacing: 4) {
                    ForEach(0..<3) { index in
                        RoundedRectangle(cornerRadius: 8)
                            .fill(Color.brandPrimary.opacity(0.6 + Double(index) * 0.15))
                            .frame(width: 12, height: 12)
                            .offset(y: reduceMotion ? 0 : bubbleOffset)
                            .animation(
                                reduceMotion ? nil : AppMotion.loadingBubblePulse
                                    .delay(Double(index) * 0.15),
                                value: bubbleOffset
                            )
                    }
                }
            }
            .onAppear {
                guard !reduceMotion else { return }
                bubbleOffset = -8
                withAnimation(AppMotion.chatIllustrationPulse) {
                    scale = 1.1
                }
            }
            .onChange(of: reduceMotion) { _, reduceMotion in
                if reduceMotion {
                    bubbleOffset = 0
                    scale = 1.0
                } else {
                    bubbleOffset = -8
                    withAnimation(AppMotion.chatIllustrationPulse) {
                        scale = 1.1
                    }
                }
            }

            // Simple loading text
            Text("Loading conversation")
                .font(.appSubheadline)
                .foregroundColor(Color.onSurfaceSecondary)
        }
    }
}

#Preview {
    ChatLoadingView()
}
