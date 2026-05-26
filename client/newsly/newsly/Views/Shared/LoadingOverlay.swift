//
//  LoadingOverlay.swift
//  newsly
//
//  Extracted from OnboardingFlowView for reuse.
//

import SwiftUI

struct LoadingOverlay: View {
    let message: String

    var body: some View {
        VStack(spacing: 10) {
            ProgressView()
                .tint(.onboardingText)
            Text(message)
                .font(.callout)
                .foregroundColor(.onboardingText.opacity(0.6))
        }
        .padding(20)
        .glassCard(cornerRadius: 14)
        .shadow(color: .black.opacity(0.08), radius: 8, x: 0, y: 4)
    }
}
