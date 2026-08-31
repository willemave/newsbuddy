//
//  OnboardingIntroStep.swift
//  newsly
//

import SwiftUI

/// The buddy's first appearance. It introduces itself in the first person, which doubles as
/// the clearest one-sentence description of the product. This is the only place the character
/// gets the full frame — everywhere else it appears at button size.
struct OnboardingIntroStep: View {
    let viewModel: OnboardingViewModel

    @State private var arrived = false
    @State private var greeting = false

    var body: some View {
        VStack(spacing: 0) {
            Spacer()

            VStack(spacing: 30) {
                Image("BuddyMark")
                    .resizable()
                    .aspectRatio(contentMode: .fit)
                    .frame(width: 168, height: 168)
                    .appShadow(.elevated)
                    // A small settle on arrival, then one slow tilt — enough to read as
                    // alive without becoming an animation the user has to wait through.
                    .rotationEffect(.degrees(greeting ? -4 : 0), anchor: .bottom)
                    .scaleEffect(arrived ? 1 : 0.86)
                    .opacity(arrived ? 1 : 0)
                    .accessibilityLabel("Newsbuddy")

                VStack(spacing: 12) {
                    Text("HELLO")
                        .font(.editorialMeta)
                        .tracking(1.5)
                        .foregroundColor(.onSurfaceSecondary)
                    Text("I'm your buddy.")
                        .font(.onboardingDisplay)
                        .foregroundColor(.onSurface)
                        .multilineTextAlignment(.center)
                        .accessibilityIdentifier("onboarding.intro.screen")
                    Text("I read your sources each morning and write you one briefing.")
                    .font(.onboardingSubtitle)
                    .foregroundColor(.onSurfaceSecondary)
                    .multilineTextAlignment(.center)
                    .lineSpacing(3)

                    Rectangle()
                        .fill(Color.outlineVariant)
                        .frame(width: 54, height: 1)
                        .padding(.top, 4)
                }
                .opacity(arrived ? 1 : 0)
                .offset(y: arrived ? 0 : 10)
            }

            Spacer()

            Button {
                withAnimation(AppMotion.panel) {
                    viewModel.advanceToChoice()
                }
            } label: {
                Text("Nice to meet you")
                    .font(.appCallout.weight(.semibold))
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 14)
                    .foregroundColor(.surfacePrimary)
                    .background(primaryButtonBackground)
            }
            .buttonStyle(OnboardingPrimaryPressStyle())
            .accessibilityIdentifier("onboarding.intro.continue")
        }
        .padding(24)
        .padding(.bottom, 16)
        .onAppear {
            withAnimation(.spring(response: 0.62, dampingFraction: 0.72)) { arrived = true }
            withAnimation(
                .easeInOut(duration: 1.6).repeatForever(autoreverses: true).delay(0.5)
            ) {
                greeting = true
            }
        }
    }
}
