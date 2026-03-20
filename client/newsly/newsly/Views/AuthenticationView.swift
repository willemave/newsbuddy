//
//  AuthenticationView.swift
//  newsly
//
//  Created by Assistant on 10/25/25.
//

import SwiftUI
import AuthenticationServices

/// Login screen with Apple Sign In
struct AuthenticationView: View {
    @EnvironmentObject var authViewModel: AuthenticationViewModel
    @State private var showingDebugMenu = false
    @State private var tapCount = 0
    @State private var lastTapTime: Date?

    var body: some View {
        VStack(spacing: 24) {
            Spacer()

            // App logo or title
            VStack(spacing: 8) {
                Image(systemName: "newspaper.fill")
                    .font(.system(size: 60))
                    .foregroundColor(.blue)

                Text("WillemNews")
                    .font(.largeTitle)
                    .fontWeight(.bold)
            }
            .contentShape(Rectangle())
            .onTapGesture {
                handleLogoTap()
            }

            Spacer()

            // Sign in with Apple button
            SignInWithAppleButton(
                .signIn,
                onRequest: { request in
                    // Configuration handled by AuthenticationService
                },
                onCompletion: { result in
                    // Handled by AuthenticationService
                }
            )
            .signInWithAppleButtonStyle(.black)
            .frame(height: 50)
            .padding(.horizontal, 40)
            .onTapGesture {
                authViewModel.signInWithApple()
            }

            // Error message
            if let errorMessage = authViewModel.errorMessage {
                Text(errorMessage)
                    .foregroundColor(.red)
                    .font(.caption)
                    .padding(.horizontal, 40)
            }

            Spacer()
        }
        .padding()
        .sheet(isPresented: $showingDebugMenu) {
            DebugMenuView()
                .environmentObject(authViewModel)
        }
    }

    /// Handle tap on WillemNews icon - show debug menu after 3 taps within 2 seconds
    private func handleLogoTap() {
        let now = Date()

        // Reset tap count if too much time has passed since last tap (2 seconds)
        if let lastTap = lastTapTime, now.timeIntervalSince(lastTap) > 2.0 {
            tapCount = 0
        }

        tapCount += 1
        lastTapTime = now

        // Show debug menu after 3 taps
        if tapCount >= 3 {
            showingDebugMenu = true
            tapCount = 0
            lastTapTime = nil
        }
    }
}

#Preview {
    AuthenticationView()
        .environmentObject(AuthenticationViewModel())
}
