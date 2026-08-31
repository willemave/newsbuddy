import Observation

/// Process-lifetime composition root.
///
/// It owns lifecycle facts and exactly one authenticated-user scope. It does
/// not expose a dependency lookup surface to feature code.
@MainActor
@Observable
final class AppRuntime {
    struct Dependencies {
        let lifecycle: AppLifecycle
        let authenticationController: AuthenticationController
        let makeAuthenticatedSession: @MainActor (User) -> AuthenticatedSession
    }

    let lifecycle: AppLifecycle
    let authenticationController: AuthenticationController
    private(set) var authenticatedSession: AuthenticatedSession?

    @ObservationIgnored
    private let makeAuthenticatedSession: @MainActor (User) -> AuthenticatedSession

    init(dependencies: Dependencies) {
        self.lifecycle = dependencies.lifecycle
        self.authenticationController = dependencies.authenticationController
        self.makeAuthenticatedSession = dependencies.makeAuthenticatedSession
    }

    @discardableResult
    func establishSession(for user: User) -> AuthenticatedSession {
        if let authenticatedSession, authenticatedSession.user.id == user.id {
            authenticatedSession.updateUser(user)
            authenticatedSession.synchronize(with: lifecycle)
            return authenticatedSession
        }

        authenticatedSession?.detach()
        let session = makeAuthenticatedSession(user)
        authenticatedSession = session
        session.synchronize(with: lifecycle)
        return session
    }

    func clearAuthenticatedSession() {
        authenticatedSession?.detach()
        authenticatedSession = nil
    }

    /// The app root is the only product-level lifecycle writer.
    func record(_ phase: AppLifecycle.Phase) {
        lifecycle.record(phase)
        if phase == .active {
            authenticationController.resumeRestorationIfNeeded(
                for: lifecycle.activation
            )
        }
        authenticatedSession?.synchronize(with: lifecycle)
    }
}
