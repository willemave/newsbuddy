//
//  LandingView.swift
//  newsly
//

import SwiftUI

struct LandingView: View {
    @Environment(AuthenticationViewModel.self) private var authViewModel
    #if DEBUG && targetEnvironment(simulator)
    @State private var showingDebugMenu = false
    @State private var tapCount = 0
    @State private var lastTapTime: Date?
    #endif

    private var isLoading: Bool {
        if case .loading = authViewModel.authState { return true }
        return false
    }

    var body: some View {
        ZStack {
            Color.surfacePrimary.ignoresSafeArea()

            VStack(spacing: 0) {
                Spacer()

                titleSection

                Spacer()

                bottomCard
            }
        }
        #if DEBUG && targetEnvironment(simulator)
        .sheet(isPresented: $showingDebugMenu) {
            DebugMenuView()
                .environment(authViewModel)
        }
        #endif
    }

    // MARK: - Title

    private var titleSection: some View {
        titleContent()
    }

    private func titleContent() -> some View {
        VStack(spacing: 24) {
            Image("BuddyMark")
                .resizable()
                .aspectRatio(contentMode: .fit)
                .frame(width: 220, height: 220)
                #if DEBUG && targetEnvironment(simulator)
                .onTapGesture {
                    handleLogoTap()
                }
                #endif
                .accessibilityLabel("Newsbuddy")

            VStack(spacing: 10) {
                Text("Newsbuddy")
                    .font(.onboardingDisplay)
                    .foregroundColor(.onSurface)
                    .accessibilityIdentifier("auth.landing.screen")

                Text("Your quiet news companion.\nOne briefing, read across your sources.")
                    .font(.onboardingSubtitle)
                    .foregroundColor(.onSurfaceSecondary)
                    .multilineTextAlignment(.center)
                    .lineSpacing(3)
            }
        }
    }

    // MARK: - Bottom Card

    private var bottomCard: some View {
        VStack(spacing: 16) {
            Button(action: { authViewModel.signInWithApple() }) {
                ZStack {
                    if isLoading {
                        ProgressView()
                            .tint(.onboardingSurface)
                    } else {
                        HStack(spacing: 8) {
                            Image(systemName: "apple.logo")
                                .font(.appBody.weight(.medium))
                            Text("Continue with Apple")
                                .font(.appCallout.weight(.semibold))
                        }
                        .foregroundColor(.onboardingSurface)
                    }
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 16)
                .background(Color.onboardingText)
                .clipShape(RoundedRectangle(cornerRadius: 24))
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("auth.continue_with_apple")
            .disabled(isLoading)

            Text("Newsbuddy uses external AI services to process content and instructions you submit. By continuing, you agree to the Terms and acknowledge the Privacy Policy.")
                .font(.appCaption)
                .foregroundColor(.onboardingText.opacity(0.62))
                .multilineTextAlignment(.center)

            HStack(spacing: 16) {
                Link("Privacy", destination: URL(string: "https://news.willemsavenue.com/privacy")!)
                Link("Terms", destination: URL(string: "https://news.willemsavenue.com/terms")!)
                Link("Support", destination: URL(string: "https://news.willemsavenue.com/support")!)
            }
            .font(.appCaption.weight(.semibold))
            .tint(.onboardingText)

            if let errorMessage = authViewModel.errorMessage {
                Text(errorMessage)
                    .font(.appCaption)
                    .foregroundColor(.statusDestructive)
                    .multilineTextAlignment(.center)
            }
        }
        .padding(24)
        .glassCard(cornerRadius: 40)
        .padding(.horizontal, Spacing.appHorizontalMargin)
        .padding(.bottom, 16)
    }

    // MARK: - Debug

    #if DEBUG && targetEnvironment(simulator)
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
    #endif
}
