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
        // Hairline rules rather than fat capsules — the same 1px vocabulary the reader
        // uses for its section rules and timeline separators.
        HStack(spacing: 6) {
            ForEach(0..<progressStepTotal, id: \.self) { index in
                Rectangle()
                    .fill(index < currentStepInfo.number ? Color.brandPrimary : Color.outlineVariant)
                    .frame(height: index < currentStepInfo.number ? 2 : 1)
            }
        }
        .frame(height: 2)
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
