//
//  OnboardingProgressHeader.swift
//  newsly
//

import SwiftUI

struct OnboardingProgressHeader: View {
    let step: OnboardingStep
    let reduceMotion: Bool

    private let progressStepTotal = 5

    var body: some View {
        HStack(spacing: 6) {
            ForEach(0..<progressStepTotal, id: \.self) { index in
                Capsule()
                    .fill(
                        index < currentStepInfo.number
                            ? Color.onboardingText.opacity(0.55)
                            : Color.onboardingText.opacity(0.14)
                    )
                    .frame(height: 4)
            }
        }
        .animation(
            AppMotion.respectingReduceMotion(reduceMotion, AppMotion.emphasized),
            value: currentStepInfo.number
        )
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(
            "Step \(currentStepInfo.number) of \(progressStepTotal), \(currentStepInfo.label)"
        )
    }

    private var currentStepInfo: (number: Int, label: String) {
        switch step {
        case .intro:
            return (1, "Say hello")
        case .choice:
            return (1, "Choose your start")
        case .audio, .loading:
            return (2, step == .audio ? "Voice setup" : "Matching sources")
        case .suggestions:
            return (3, "Review picks")
        case .fastNews, .aggregators:
            return (4, "News aggregators")
        case .reddit:
            return (5, "Reddit")
        }
    }
}
