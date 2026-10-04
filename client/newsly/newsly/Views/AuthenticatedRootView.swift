//
//  AuthenticatedRootView.swift
//  newsly
//
//  Created by Assistant on 1/17/26.
//

import OSLog
import SwiftUI

private let authenticatedRootLogger = Logger(
    subsystem: "com.newsly",
    category: "AuthenticatedRootView"
)

private enum AuthenticatedPresentationState {
    case deciding
    case onboarding
    case content
}

struct AuthenticatedRootView: View {
    @Environment(AuthenticationController.self) private var authViewModel
    let session: AuthenticatedSession

    @State private var presentationState: AuthenticatedPresentationState = .deciding

    private var user: User {
        session.user
    }

    var body: some View {
        Group {
            switch presentationState {
            case .deciding:
                LoadingView()
            case .onboarding:
                OnboardingFlowView(
                    viewModel: session.dependencyFactory.makeOnboardingViewModel(user: user)
                ) { response in
                    authViewModel.updateUser(updatedUser(from: response))
                    Task {
                        do {
                            let updatedUser = try await session.dependencyFactory
                                .authenticationService.getCurrentUser()
                            authViewModel.updateUser(updatedUser)
                        } catch {
                            authenticatedRootLogger.error(
                                "Failed to refresh user after onboarding: \(error.localizedDescription, privacy: .public)"
                            )
                        }
                    }
                    presentationState = .content
                }
            case .content:
                ContentView(session: session)
                    .id(user.id)
                    .environment(authViewModel)
                    .withToast(service: session.dependencyFactory.toastService)
                    .task {
                        guard !E2ETestLaunch.isEnabled else { return }
                        await session.dependencyFactory.localNotificationService
                            .requestAuthorization()
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
    }

    private func updatePresentation() {
        if !user.hasCompletedOnboarding {
            presentationState = .onboarding
            return
        }

        presentationState = .content
    }

    private func updatedUser(from response: OnboardingCompleteResponse) -> User {
        User(
            id: user.id,
            appleId: user.appleId,
            email: user.email,
            fullName: user.fullName,
            twitterUsername: user.twitterUsername,
            hasXBookmarkSync: user.hasXBookmarkSync,
            isAdmin: user.isAdmin,
            isActive: user.isActive,
            hasCompletedOnboarding: response.hasCompletedOnboarding,
            hasCompletedNewUserTutorial: response.hasCompletedNewUserTutorial,
            readingExperience: user.readingExperience,
            timezone: user.timezone,
            timezoneRevision: user.timezoneRevision,
            createdAt: user.createdAt,
            updatedAt: user.updatedAt
        )
    }

}
