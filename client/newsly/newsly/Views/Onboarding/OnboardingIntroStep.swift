//
//  OnboardingIntroStep.swift
//  newsly
//

import SwiftUI

/// The guide arrives, blinks and docks itself in the flow-level overlay while this screen
/// introduces it in the first person, so the copy is never waiting on the animation.
struct OnboardingIntroStep: View {
    let viewModel: OnboardingViewModel

    @State private var arrived = false

    var body: some View {
        VStack(spacing: 0) {
            Spacer()

            VStack(spacing: 30) {
                VStack(spacing: 12) {
                    Text("HELLO")
                        .font(.editorialMeta)
                        .tracking(1.5)
                        .foregroundColor(.onSurfaceSecondary)
                    Text("I'm your news buddy,")
                        .font(.onboardingDisplay)
                        .foregroundColor(.onSurface)
                        .multilineTextAlignment(.center)
                        .accessibilityIdentifier("onboarding.intro.screen")
                    Text("giving you calm news in a hectic world.")
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
                Text("Continue")
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
        }
    }
}
