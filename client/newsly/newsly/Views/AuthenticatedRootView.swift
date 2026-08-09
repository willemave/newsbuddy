//
//  AuthenticatedRootView.swift
//  newsly
//
//  Created by Assistant on 1/17/26.
//

import SwiftUI

private enum AuthenticatedPresentationState {
    case deciding
    case onboarding
    case content
}

struct AuthenticatedRootView: View {
    @Environment(AuthenticationViewModel.self) private var authViewModel
    let user: User

    @State private var presentationState: AuthenticatedPresentationState = .deciding
    var body: some View {
        Group {
            switch presentationState {
            case .deciding:
                LoadingView()
            case .onboarding:
                OnboardingFlowView(user: user) { response in
                    // Refresh user from server to pick up updated has_completed_onboarding
                    Task {
                        if let updatedUser = try? await AuthenticationService.shared.getCurrentUser() {
                            authViewModel.updateUser(updatedUser)
                        } else {
                            authViewModel.updateUser(updatedUserOnboardingFlag(true))
                        }
                    }
                    presentationState = .content
                }
            case .content:
                ContentView(userId: user.id)
                    .id(user.id)
                    .environment(authViewModel)
                    .withToast()
                    .task {
                        guard !E2ETestLaunch.isEnabled else { return }
                        await LocalNotificationService.shared.requestAuthorization()
                    }
            }
        }
        .onAppear {
            updatePresentation()
        }
        .onChange(of: user.id) { _, _ in
            updatePresentation()
        }
        .onChange(of: user.hasCompletedOnboarding) { _, _ in
            updatePresentation()
        }
        .onChange(of: user.hasCompletedNewUserTutorial) { _, _ in
            updatePresentation()
        }
    }

    private func updatePresentation() {
        if !user.hasCompletedOnboarding {
            presentationState = .onboarding
            return
        }

        presentationState = .content
    }

    private func updatedUserOnboardingFlag(_ completed: Bool) -> User {
        User(
            id: user.id,
            appleId: user.appleId,
            email: user.email,
            fullName: user.fullName,
            twitterUsername: user.twitterUsername,
            hasXBookmarkSync: user.hasXBookmarkSync,
            isAdmin: user.isAdmin,
            isActive: user.isActive,
            hasCompletedOnboarding: completed,
            hasCompletedNewUserTutorial: user.hasCompletedNewUserTutorial,
            readingExperience: user.readingExperience,
            createdAt: user.createdAt,
            updatedAt: user.updatedAt
        )
    }

}
