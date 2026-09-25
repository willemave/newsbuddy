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

            // The docked guide stays visible in the upper-left, so this screen can ask
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
                    Text("Tell me what you like to read and I'll find newsletters, podcasts and communities to match. It takes about a minute.")
                        .font(.onboardingSubtitle)
                        .foregroundColor(.onSurfaceSecondary)
                        .multilineTextAlignment(.center)
                        .lineSpacing(3)
                        .padding(.horizontal, 8)
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

                // Skipping is a valid start but not the recommended one, so it reads as the
                // quieter choice.
                Button("Skip for now") {
                    viewModel.chooseDefaults()
                }
                .font(.appCallout.weight(.medium))
                .foregroundColor(.onSurfaceSecondary)
                .buttonStyle(OnboardingTextButtonStyle())
                .accessibilityIdentifier("onboarding.choice.skip")
            }

            if let error = viewModel.errorMessage {
                Text(error)
                    .font(.appCaption)
                    .foregroundColor(.statusDestructive)
                    .padding(.top, 8)
            }
        }
        .padding(.horizontal, Spacing.appHorizontalMargin)
        .padding(.vertical, 24)
        .padding(.bottom, 8)
    }
}
