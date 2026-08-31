//
//  LoadingView.swift
//  newsly
//
//  Created by Assistant on 7/8/25.
//

import SwiftUI

/// Shown while the session is restored at launch. Leads with the app icon rather than a
/// spinner so the first frame of every cold start is the brand.
struct LoadingView: View {
    @State private var settled = false

    var body: some View {
        VStack(spacing: 20) {
            Image("AppMark")
                .resizable()
                .aspectRatio(contentMode: .fit)
                .frame(width: 104, height: 104)
                .clipShape(RoundedRectangle(cornerRadius: 23, style: .continuous))
                .shadow(color: Color.black.opacity(0.12), radius: 18, x: 0, y: 8)
                .scaleEffect(settled ? 1 : 0.94)
                .opacity(settled ? 1 : 0)
                .accessibilityLabel("Newsbuddy")

            ProgressView()
                .opacity(settled ? 1 : 0)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color.surfacePrimary)
        .onAppear {
            withAnimation(.easeOut(duration: 0.45)) { settled = true }
        }
    }
}