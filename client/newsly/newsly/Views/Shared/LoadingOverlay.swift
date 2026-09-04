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
                .foregroundColor(.onSurfaceSecondary)
        }
        .padding(20)
        .glassCard(cornerRadius: 14)
        .appShadow(.floating)
    }
}
