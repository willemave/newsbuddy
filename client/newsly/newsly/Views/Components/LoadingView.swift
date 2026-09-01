//
//  LoadingView.swift
//  newsly
//
//  Created by Assistant on 7/8/25.
//

import SwiftUI

/// Shown while the session is restored at launch. Leads with the app icon rather than a
/// spinner so the first frame of every cold start is the brand.
struct LoadingView: View {
    @State private var settled = false

    var body: some View {
        VStack(spacing: 20) {
            Image("AppMark")
                .resizable()
                .aspectRatio(contentMode: .fit)
                .frame(width: 104, height: 104)
                .clipShape(RoundedRectangle(cornerRadius: 23, style: .continuous))
                .shadow(color: Color.black.opacity(0.12), radius: 18, x: 0, y: 8)
                .scaleEffect(settled ? 1 : 0.94)
                .opacity(settled ? 1 : 0)
                .accessibilityLabel("Newsbuddy")

            ProgressView()
                .opacity(settled ? 1 : 0)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color.surfacePrimary)
        .onAppear {
            withAnimation(.easeOut(duration: 0.45)) { settled = true }
        }
    }
}

/// The Buddy replaces generic spinners in product-owned waiting states. Its slow
/// breathing motion keeps the interface alive without implying determinate progress.
struct BuddyLoadingIndicator: View {
    let size: CGFloat

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var breathing = false

    init(size: CGFloat = 84) {
        self.size = size
    }

    var body: some View {
        ZStack {
            Circle()
                .stroke(Color.brandPrimary.opacity(0.12), lineWidth: 1)
                .frame(width: size * 1.12, height: size * 1.12)
                .scaleEffect(breathing ? 1.06 : 0.92)
                .opacity(breathing ? 0.25 : 0.7)

            Image("BuddyMark")
                .resizable()
                .aspectRatio(contentMode: .fit)
                .frame(width: size, height: size)
                .appShadow(.floating)
                .scaleEffect(breathing ? 1.03 : 0.97)
                .offset(y: breathing ? -3 : 2)
                .rotationEffect(.degrees(breathing ? 1.5 : -1.5), anchor: .bottom)
        }
        .frame(width: size * 1.18, height: size * 1.18)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Newsbuddy is working")
        .onAppear {
            updateAnimation(for: reduceMotion)
        }
        .onChange(of: reduceMotion) { _, reduceMotion in
            updateAnimation(for: reduceMotion)
        }
    }

    private func updateAnimation(for reduceMotion: Bool) {
        if reduceMotion {
            breathing = false
        } else {
            withAnimation(.easeInOut(duration: 1.25).repeatForever(autoreverses: true)) {
                breathing = true
            }
        }
    }
}

struct BuddyLoadingView: View {
    let message: String

    var body: some View {
        VStack(spacing: 18) {
            BuddyLoadingIndicator(size: 96)

            Text(message)
                .font(.appCallout.weight(.medium))
                .foregroundStyle(Color.onSurfaceSecondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color.surfacePrimary)
        .accessibilityIdentifier("briefing.loading")
    }
}
