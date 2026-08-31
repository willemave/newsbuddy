import XCTest
@testable import newsly

@MainActor
final class AppLifecycleTests: XCTestCase {
    func testFirstActivePhaseCreatesInitialActivation() {
        let initialDate = Date(timeIntervalSince1970: 1_000)
        let lifecycle = AppLifecycle(now: { initialDate })

        lifecycle.record(.active)

        XCTAssertEqual(lifecycle.phase, .active)
        XCTAssertEqual(
            lifecycle.activation,
            AppLifecycle.Activation(
                generation: 1,
                kind: .initialLaunch,
                occurredAt: initialDate,
                backgroundDuration: nil
            )
        )
        XCTAssertNil(lifecycle.lastInterruptionReturnAt)
    }

    func testInterruptionReturnDoesNotAdvanceActivationGeneration() {
        var currentDate = Date(timeIntervalSince1970: 1_000)
        let lifecycle = AppLifecycle(now: { currentDate })
        lifecycle.record(.active)
        let initialActivation = lifecycle.activation

        currentDate = currentDate.addingTimeInterval(2)
        lifecycle.record(.inactive)
        currentDate = currentDate.addingTimeInterval(3)
        lifecycle.record(.active)

        XCTAssertEqual(lifecycle.activation, initialActivation)
        XCTAssertEqual(lifecycle.lastInterruptionReturnAt, currentDate)
    }

    func testTrueBackgroundCreatesOneWarmResumeGeneration() {
        var currentDate = Date(timeIntervalSince1970: 1_000)
        let lifecycle = AppLifecycle(now: { currentDate })
        lifecycle.record(.active)

        currentDate = currentDate.addingTimeInterval(1)
        lifecycle.record(.inactive)
        currentDate = currentDate.addingTimeInterval(1)
        lifecycle.record(.background)
        currentDate = currentDate.addingTimeInterval(8)
        lifecycle.record(.inactive)
        currentDate = currentDate.addingTimeInterval(2)
        lifecycle.record(.active)

        XCTAssertEqual(lifecycle.activation?.generation, 2)
        XCTAssertEqual(lifecycle.activation?.kind, .warmResume)
        XCTAssertEqual(lifecycle.activation?.occurredAt, currentDate)
        XCTAssertEqual(lifecycle.activation?.backgroundDuration, .seconds(10))
    }

    func testDuplicatePhaseWritesAreIdempotent() {
        var currentDate = Date(timeIntervalSince1970: 1_000)
        let lifecycle = AppLifecycle(now: { currentDate })
        lifecycle.record(.active)
        let initialActivation = lifecycle.activation

        currentDate = currentDate.addingTimeInterval(5)
        lifecycle.record(.active)

        XCTAssertEqual(lifecycle.activation, initialActivation)
        XCTAssertNil(lifecycle.lastInterruptionReturnAt)
    }

    func testRuntimeRetainsInjectedLifecycle() {
        let lifecycle = AppLifecycle()
        let runtime = AppRuntime(
            dependencies: AppRuntime.Dependencies(
                lifecycle: lifecycle,
                authenticationController: makeAuthenticationController(),
                makeAuthenticatedSession: { _ in
                    fatalError("This test does not establish a session")
                }
            )
        )

        XCTAssertTrue(runtime.lifecycle === lifecycle)
    }

    func testRuntimeReusesMatchingSessionAndReplacesAccountScope() {
        let lifecycle = AppLifecycle()
        let dependencyFactory = makeRootDependencyFactory()
        var createdUserIDs: [Int] = []
        let runtime = AppRuntime(
            dependencies: AppRuntime.Dependencies(
                lifecycle: lifecycle,
                authenticationController: makeAuthenticationController(),
                makeAuthenticatedSession: { user in
                    createdUserIDs.append(user.id)
                    return dependencyFactory.makeAuthenticatedSession(user: user)
                }
            )
        )

        let original = runtime.establishSession(for: makeUser(id: 41, fullName: "Original"))
        let updated = runtime.establishSession(for: makeUser(id: 41, fullName: "Updated"))

        XCTAssertTrue(original === updated)
        XCTAssertEqual(updated.user.fullName, "Updated")
        XCTAssertEqual(createdUserIDs, [41])

        let replacement = runtime.establishSession(for: makeUser(id: 42, fullName: "Other"))

        XCTAssertFalse(original === replacement)
        XCTAssertEqual(createdUserIDs, [41, 42])
        XCTAssertTrue(runtime.authenticatedSession === replacement)

        runtime.clearAuthenticatedSession()
        XCTAssertNil(runtime.authenticatedSession)
    }

    private func makeUser(id: Int, fullName: String) -> User {
        User(
            id: id,
            appleId: "apple-\(id)",
            email: "reader\(id)@example.com",
            fullName: fullName,
            twitterUsername: nil,
            hasXBookmarkSync: false,
            isAdmin: false,
            isActive: true,
            hasCompletedOnboarding: true,
            hasCompletedNewUserTutorial: true,
            createdAt: Date(timeIntervalSince1970: 1_700_000_000),
            updatedAt: Date(timeIntervalSince1970: 1_700_000_001)
        )
    }
}

@MainActor
private func makeAuthenticationController() -> AuthenticationController {
    AuthenticationViewModel(
        authService: AuthenticationTestService(
            currentUserResult: .failure(AuthError.notAuthenticated)
        ),
        tokenStore: AuthenticationTestTokenStore(
            accessToken: nil,
            refreshToken: nil,
            userID: nil
        ),
        userCache: AuthenticationTestUserCache(user: nil)
    )
}

@MainActor
private func makeRootDependencyFactory() -> RootDependencyFactory {
    let apiClient = APIClient.shared
    let onboardingService = OnboardingService.shared
    return RootDependencyFactory(
        dependencies: RootDependencyFactory.Dependencies(
            apiClient: apiClient,
            authenticationService: AuthenticationService.shared,
            tokenStore: KeychainManager.shared,
            credentialSession: CredentialSession.shared,
            chatService: ChatService.shared,
            contentService: ContentService.shared,
            scraperConfigService: ScraperConfigService.shared,
            toastService: ToastService.shared,
            briefingService: LiveBriefingService(
                apiClient: apiClient,
                completeFirstRun: {
                    _ = try await onboardingService.markTutorialComplete()
                }
            ),
            narrationPlaybackService: NarrationPlaybackService.shared,
            audioEpisodeService: AudioEpisodeService.shared,
            onboardingService: onboardingService,
            onboardingStateStore: OnboardingStateStore(
                defaults: SharedContainer.userDefaults
            ),
            learningDeckService: LearningDeckService.shared,
            learningDeckStatusRegistry: LearningDeckStatusRegistry.shared,
            twitterShareService: TwitterShareService.shared,
            openAIService: OpenAIService.shared,
            appSettings: AppSettings.shared,
            xIntegrationService: XIntegrationService.shared,
            feedbackService: FeedbackService.shared,
            cliLinkService: CLILinkService(client: apiClient),
            localNotificationService: LocalNotificationService.shared,
            sharedDefaults: SharedContainer.userDefaults,
            makeVoiceDictationTranscriber: {
                SpeechTranscriberFactory.makeVoiceDictationTranscriber()
            },
            makeChatNavigationCoordinator: { ChatNavigationCoordinator() }
        )
    )
}
