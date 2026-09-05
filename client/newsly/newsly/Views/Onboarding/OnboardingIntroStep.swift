import SwiftUI

/// The welcome remains interactive until the user chooses to continue.
struct OnboardingIntroStep: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    let viewModel: OnboardingViewModel
    let logoNamespace: Namespace.ID

    var body: some View {
        GeometryReader { proxy in
            ScrollView {
                VStack(spacing: 24) {
                    Spacer(minLength: 0)
                    InteractiveLogoView()
                        .frame(height: min(340, max(220, proxy.size.height * 0.46)))
                        .matchedGeometryEffect(id: "welcomeBuddy", in: logoNamespace)

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
                    }
                    .padding(.horizontal, 24)

                    if !reduceMotion {
                        Text("Give me a spin")
                            .font(.appCaption)
                            .foregroundColor(.onSurfaceSecondary)
                    }
                    Spacer(minLength: 0)
                    Button {
                        withAnimation(reduceMotion ? nil : AppMotion.panel) {
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
                    .padding(.horizontal, 24)
                    .padding(.bottom, 24)
                }
                .frame(maxWidth: .infinity)
                .frame(minHeight: proxy.size.height)
            }
            .scrollIndicators(.hidden)
        }
    }
}
