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

            VStack(spacing: 32) {
                Image("Mascot")
                    .resizable()
                    .aspectRatio(contentMode: .fit)
                    .frame(width: 180, height: 180)
                    .appShadow(.elevated)
                    .accessibilityLabel("Newsbuddy mascot")

                VStack(spacing: 12) {
                    Text("MEET YOUR GUIDE")
                        .font(.editorialMeta)
                        .tracking(1.8)
                        .foregroundColor(.onboardingText.opacity(0.55))
                    Text("Newsbuddy")
                        .font(.watercolorDisplay)
                        .foregroundColor(.onboardingText)
                        .multilineTextAlignment(.center)
                        .accessibilityIdentifier("onboarding.choice.screen")
                    Text("I'm going to help you get onboarded.\nLet's get going.")
                        .font(.watercolorSubtitle)
                        .foregroundColor(.onboardingText.opacity(0.74))
                        .multilineTextAlignment(.center)
                        .lineSpacing(3)
                }
            }

            Spacer()

            VStack(spacing: 12) {
                Button {
                    withAnimation(AppMotion.panel) {
                        viewModel.startPersonalized()
                    }
                } label: {
                    HStack(spacing: 8) {
                        Image(systemName: "mic.fill")
                            .font(.appBody.weight(.medium))
                        Text("Personalize with voice")
                            .font(.appCallout.weight(.semibold))
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 14)
                    .foregroundColor(.onboardingSurface)
                    .background(primaryButtonBackground)
                }
                .buttonStyle(OnboardingPrimaryPressStyle())
                .accessibilityIdentifier("onboarding.choice.personalized")

                Button {
                    viewModel.chooseDefaults()
                } label: {
                    Text("Skip personalization")
                        .font(.appCallout.weight(.medium))
                        .foregroundColor(.onboardingText.opacity(0.72))
                }
                .buttonStyle(OnboardingTextButtonStyle())
                .accessibilityIdentifier("onboarding.choice.skip")
            }
            .padding(12)
            .background(cardSurface(cornerRadius: 36))

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
