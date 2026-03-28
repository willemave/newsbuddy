//
//  ChatLoadingView.swift
//  newsly
//
//  Created by Assistant on 11/28/25.
//

import SwiftUI

struct ChatLoadingView: View {
    @State private var rotation: Double = 0
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
                            colors: [Color.terracottaPrimary.opacity(0.15), Color.clear],
                            center: .center,
                            startRadius: 20,
                            endRadius: 60
                        )
                    )
                    .frame(width: 120, height: 120)
                    .scaleEffect(scale)

                // Three floating chat bubbles
                HStack(spacing: 4) {
                    ForEach(0..<3) { index in
                        RoundedRectangle(cornerRadius: 8)
                            .fill(Color.terracottaPrimary.opacity(0.6 + Double(index) * 0.15))
                            .frame(width: 12, height: 12)
                            .offset(y: bubbleOffset)
                            .animation(
                                .easeInOut(duration: 0.5)
                                    .repeatForever(autoreverses: true)
                                    .delay(Double(index) * 0.15),
                                value: bubbleOffset
                            )
                    }
                }
            }
            .onAppear {
                bubbleOffset = -8
                withAnimation(.easeInOut(duration: 2.0).repeatForever(autoreverses: true)) {
                    scale = 1.1
                }
            }

            // Simple loading text
            Text("Loading conversation")
                .font(.subheadline)
                .foregroundColor(Color.onSurfaceSecondary)
        }
    }
}

#Preview {
    ChatLoadingView()
}
