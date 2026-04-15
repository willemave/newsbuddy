//
//  LandingView.swift
//  newsly
//

import SwiftUI

struct LandingView: View {
    @EnvironmentObject var authViewModel: AuthenticationViewModel
    @State private var showingDebugMenu = false
    @State private var tapCount = 0
    @State private var lastTapTime: Date?

    private var isLoading: Bool {
        if case .loading = authViewModel.authState { return true }
        return false
    }

    var body: some View {
        ZStack {
            WatercolorBackground(energy: 0.15)

            VStack(spacing: 0) {
                Spacer()

                titleSection

                Spacer()

                bottomCard
            }
        }
        .accessibilityIdentifier("auth.landing.screen")
        .preferredColorScheme(.light)
        .sheet(isPresented: $showingDebugMenu) {
            DebugMenuView()
                .environmentObject(authViewModel)
        }
    }

    // MARK: - Title

    private var titleSection: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 30.0)) { timeline in
            let t = timeline.date.timeIntervalSinceReferenceDate
            let yOffset = WatercolorBackground.titleOscillation(time: t)
            let glowColor = WatercolorBackground.titleGlowColor(time: t)

            VStack(spacing: 24) {
                Image("Mascot")
                    .resizable()
                    .aspectRatio(contentMode: .fit)
                    .frame(width: 220, height: 220)
                    .onTapGesture {
                        handleLogoTap()
                    }
                    .accessibilityLabel("Newsbuddy mascot")

                VStack(spacing: 10) {
                    Text("Newsbuddy")
                        .font(.watercolorDisplay)
                        .foregroundColor(.watercolorSlate)
                        .shadow(color: glowColor.opacity(0.6), radius: 16, x: 0, y: 0)
                        .shadow(color: glowColor.opacity(0.3), radius: 32, x: 0, y: 0)

                    Text("Your cuddly news companion.\nQuiet clarity in a noisy world.")
                        .font(.watercolorSubtitle)
                        .foregroundColor(.watercolorSlate.opacity(0.7))
                        .multilineTextAlignment(.center)
                }
            }
            .offset(y: yOffset)
        }
    }

    // MARK: - Bottom Card

    private var bottomCard: some View {
        VStack(spacing: 16) {
            Button(action: { authViewModel.signInWithApple() }) {
                ZStack {
                    if isLoading {
                        ProgressView()
                            .tint(.watercolorBase)
                    } else {
                        HStack(spacing: 8) {
                            Image(systemName: "apple.logo")
                                .font(.body.weight(.medium))
                            Text("Continue with Apple")
                                .font(.callout.weight(.semibold))
                        }
                        .foregroundColor(.watercolorBase)
                    }
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 16)
                .background(Color.watercolorSlate)
                .clipShape(RoundedRectangle(cornerRadius: 24))
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("auth.continue_with_apple")
            .disabled(isLoading)

            if let errorMessage = authViewModel.errorMessage {
                Text(errorMessage)
                    .font(.caption)
                    .foregroundColor(.red)
                    .multilineTextAlignment(.center)
            }
        }
        .padding(24)
        .glassCard(cornerRadius: 40)
        .padding(.horizontal, 20)
        .padding(.bottom, 16)
    }

    // MARK: - Debug

    private func handleLogoTap() {
        let now = Date()
        if let lastTap = lastTapTime, now.timeIntervalSince(lastTap) > 2.0 {
            tapCount = 0
        }
        tapCount += 1
        lastTapTime = now
        if tapCount >= 3 {
            showingDebugMenu = true
            tapCount = 0
            lastTapTime = nil
        }
    }
}
