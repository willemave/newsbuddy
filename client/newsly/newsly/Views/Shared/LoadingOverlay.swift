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
                .font(.appCallout)
                .foregroundColor(.onboardingText.opacity(0.6))
        }
        .padding(20)
        .glassCard(cornerRadius: 14)
        .appShadow(.floating)
    }
}
