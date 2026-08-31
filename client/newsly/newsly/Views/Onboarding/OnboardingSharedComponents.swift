//
//  OnboardingSharedComponents.swift
//  newsly
//

import SwiftUI

struct OnboardingSuggestionSection: View {
    let title: String
    let items: [OnboardingSuggestion]
    let isSelected: (OnboardingSuggestion) -> Bool
    let onToggle: (OnboardingSuggestion) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text(title)
                    .font(.editorialMeta)
                    .foregroundColor(.onboardingText.opacity(0.55))
                    .tracking(1.5)

                Spacer()

                Text("\(items.count)")
                    .font(.appCaption)
                    .monospacedDigit()
                    .foregroundColor(.onboardingText.opacity(0.45))
            }
            .padding(.top, 16)
            .padding(.bottom, 4)

            VStack(spacing: 0) {
                ForEach(Array(items.enumerated()), id: \.element.stableKey) { index, suggestion in
                    if index > 0 {
                        Rectangle()
                            .fill(Color.onboardingText.opacity(0.07))
                            .frame(height: 0.5)
                            .padding(.leading, 60)
                    }

                    OnboardingSuggestionCard(
                        suggestion: suggestion,
                        isSelected: isSelected(suggestion),
                        onToggle: { onToggle(suggestion) }
                    )
                }
            }
            .padding(.vertical, 4)
            .background(listPanelSurface)
        }
    }
}

private var listPanelSurface: some View {
    RoundedRectangle(cornerRadius: 20, style: .continuous)
        .fill(Color.onboardingSurface.opacity(0.94))
        .overlay(
            RoundedRectangle(cornerRadius: 20, style: .continuous)
                .stroke(Color.onboardingText.opacity(0.08), lineWidth: 0.5)
        )
        .appShadow(.card)
}

func onboardingPrimaryButton(_ title: String, action: @escaping () -> Void) -> some View {
    Button(action: action) {
        Text(title)
            .font(.appCallout.weight(.semibold))
            .frame(maxWidth: .infinity)
            .padding(.vertical, 14)
            .foregroundColor(.onboardingSurface)
            .background(primaryButtonBackground)
    }
    .buttonStyle(OnboardingPrimaryPressStyle())
}

func onboardingHeaderBlock(
    eyebrow: String? = nil,
    title: String,
    subtitle: String? = nil,
    isLeading: Bool = false,
    titleAccessibilityIdentifier: String? = nil
) -> some View {
    let horizontalAlignment: HorizontalAlignment = isLeading ? .leading : .center
    let textAlignment: TextAlignment = isLeading ? .leading : .center
    let frameAlignment: Alignment = isLeading ? .leading : .center

    return VStack(alignment: horizontalAlignment, spacing: 8) {
        if let eyebrow, !eyebrow.isEmpty {
            Text(eyebrow)
                .font(.editorialMeta)
                .tracking(1.5)
                .foregroundColor(.onSurfaceSecondary)
        }

        Text(title)
            .accessibilityIdentifier(ifPresent: titleAccessibilityIdentifier)
            .font(.appTitle)
            .foregroundColor(.onSurface)
            .multilineTextAlignment(textAlignment)

        if let subtitle, !subtitle.isEmpty {
            Text(subtitle)
                .font(.appCallout)
                .foregroundColor(.onSurfaceSecondary)
                .multilineTextAlignment(textAlignment)
                .lineSpacing(3)
        }

        Rectangle()
            .fill(Color.outlineVariant)
            .frame(width: 54, height: 1)
            .padding(.top, 6)
    }
    .frame(maxWidth: .infinity, alignment: frameAlignment)
}

func topicPreviewCard(
    eyebrow: String,
    title: String,
    inferredTopics: [String]
) -> some View {
    VStack(alignment: .leading, spacing: 12) {
        Text(eyebrow)
            .font(.editorialMeta)
            .tracking(1.6)
            .foregroundColor(.onboardingText.opacity(0.58))

        Text(title)
            .font(.appCallout.weight(.semibold))
            .foregroundColor(.onboardingText)
            .fixedSize(horizontal: false, vertical: true)

        if !inferredTopics.isEmpty {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(Array(inferredTopics.prefix(6)), id: \.self) { topic in
                        Text(topic)
                            .font(.appCaption.weight(.semibold))
                            .foregroundColor(.onboardingText)
                            .padding(.horizontal, 10)
                            .padding(.vertical, 8)
                            .background(Capsule().fill(Color.onboardingText.opacity(0.08)))
                    }
                }
            }
        }
    }
    .padding(18)
    .background(cardSurface(cornerRadius: 24))
}

/// Flat card on the paper ground with a hairline edge — the same treatment the rest of
/// the app uses. Corner radius is accepted for call-site compatibility but clamped to the
/// control radius so onboarding stops using its own oversized pill geometry.
func cardSurface(cornerRadius: CGFloat) -> some View {
    RoundedRectangle(cornerRadius: min(cornerRadius, CornerRadius.control), style: .continuous)
        .fill(Color.surfaceSecondary)
        .overlay(
            RoundedRectangle(cornerRadius: min(cornerRadius, CornerRadius.control), style: .continuous)
                .stroke(Color.borderSubtle, lineWidth: 1)
        )
}

var primaryButtonBackground: some View {
    RoundedRectangle(cornerRadius: CornerRadius.control, style: .continuous)
        .fill(Color.onSurface)
}

var onboardingFooterBackground: some View {
    ZStack(alignment: .top) {
        Rectangle().fill(Color.surfacePrimary)
        Rectangle().fill(Color.outlineVariant).frame(height: 0.5)
    }
    .ignoresSafeArea(edges: .bottom)
}

struct OnboardingPrimaryPressStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.96 : 1.0)
            .animation(AppMotion.press, value: configuration.isPressed)
    }
}

struct OnboardingTextButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .frame(minHeight: 44)
            .contentShape(Rectangle())
            .scaleEffect(configuration.isPressed ? 0.96 : 1.0)
            .opacity(configuration.isPressed ? 0.72 : 1.0)
            .animation(AppMotion.press, value: configuration.isPressed)
    }
}

struct OnboardingSelectionDot: View {
    let isSelected: Bool

    var body: some View {
        ZStack {
            Circle()
                .fill(
                    isSelected
                        ? Color.onboardingSelectionAccent
                        : Color.clear
                )
                .overlay(
                    Circle()
                        .strokeBorder(
                            isSelected
                                ? Color.clear
                                : Color.onboardingText.opacity(0.25),
                            lineWidth: 1.2
                        )
                )
                .frame(width: 24, height: 24)

            if isSelected {
                Image(systemName: "checkmark")
                    .font(.appSymbol(size: 11, weight: .bold))
                    .foregroundColor(.onboardingSurface)
                    .transition(.scale(scale: 0.4).combined(with: .opacity))
            }
        }
        .animation(AppMotion.press, value: isSelected)
    }
}
