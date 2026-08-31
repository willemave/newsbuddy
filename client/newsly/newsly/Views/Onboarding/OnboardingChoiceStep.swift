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

            // The buddy already introduced itself on the preceding intro step, so this
            // screen asks its question directly instead of saying hello a second time.
            VStack(spacing: 32) {
                Image("BuddyMark")
                    .resizable()
                    .aspectRatio(contentMode: .fit)
                    .frame(width: 92, height: 92)
                    .appShadow(.elevated)
                    .accessibilityLabel("Newsbuddy")

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
                    .foregroundColor(.surfacePrimary)
                    .background(primaryButtonBackground)
                }
                .buttonStyle(OnboardingPrimaryPressStyle())
                .accessibilityIdentifier("onboarding.choice.personalized")

                Button {
                    viewModel.chooseDefaults()
                } label: {
                    Text("Skip personalization")
                        .font(.appCallout.weight(.medium))
                        .foregroundColor(.onSurfaceSecondary)
                }
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
        .padding(24)
        .padding(.bottom, 16)
    }
}
