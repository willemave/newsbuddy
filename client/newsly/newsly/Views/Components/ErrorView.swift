//
//  ErrorView.swift
//  newsly
//
//  Created by Assistant on 7/8/25.
//

import SwiftUI

struct ErrorView: View {
    let message: String
    let retryAction: (() -> Void)?

    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: "exclamationmark.triangle")
                .font(.appSymbol(size: 40, weight: .light))
                .foregroundStyle(Color.statusDestructive)

            Text("Error")
                .font(.appHeadline)
                .foregroundStyle(Color.onSurface)

            Text(message)
                .font(.listSubtitle)
                .foregroundStyle(Color.onSurfaceSecondary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 280)

            if let retryAction {
                Button("Retry", action: retryAction)
                    .buttonStyle(.borderedProminent)
                    .controlSize(.small)
                    .padding(.top, 4)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color.surfacePrimary)
    }
}