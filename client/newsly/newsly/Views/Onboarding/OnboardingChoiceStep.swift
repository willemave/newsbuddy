//
//  OnboardingChoiceStep.swift
//  newsly
//

import SwiftUI

struct OnboardingChoiceStep: View {
    let viewModel: OnboardingViewModel

    var body: some View {
        VStack(spacing: 0) {
            Spacer()

            // The docked guide stays visible in the upper-right, so this screen can ask
            // its question directly without duplicating the character in the content.
            VStack(spacing: 32) {
                VStack(spacing: 12) {
                    Text("GETTING STARTED")
                        .font(.editorialMeta)
                        .tracking(1.5)
                        .foregroundColor(.onSurfaceSecondary)
                    Text("How should we begin?")
                        .font(.onboardingDisplay)
                        .foregroundColor(.onSurface)
                        .multilineTextAlignment(.center)
                        .accessibilityIdentifier("onboarding.choice.screen")
                    Rectangle()
                        .fill(Color.outlineVariant)
                        .frame(width: 54, height: 1)
                        .padding(.top, 4)
                }
            }

            Spacer()

            VStack(spacing: 12) {
                onboardingPrimaryButton("Personalize") {
                    withAnimation(AppMotion.panel) {
                        viewModel.startPersonalized()
                    }
                }
                .accessibilityIdentifier("onboarding.choice.personalized")

                onboardingPrimaryButton("Skip") {
                    viewModel.chooseDefaults()
                }
                .accessibilityIdentifier("onboarding.choice.skip")
            }

            if let error = viewModel.errorMessage {
                Text(error)
                    .font(.appCaption)
                    .foregroundColor(.statusDestructive)
                    .padding(.top, 8)
            }
        }
        .padding(24)
        .padding(.bottom, 16)
    }
}
