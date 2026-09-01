//
//  OnboardingIntroStep.swift
//  newsly
//

import SwiftUI

/// The guide has already arrived and docked in the flow-level overlay. This screen introduces
/// it in the first person while keeping the copy unobstructed during the arrival animation.
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
        }
    }
}
